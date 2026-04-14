package proxy

import (
	"context"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"sync/atomic"
	"time"

	"emby-virtual-lib/proxy/internal/config"

	log "github.com/sirupsen/logrus"
)

var defaultProxyTransport = &http.Transport{
	Proxy: http.ProxyFromEnvironment,
	DialContext: (&net.Dialer{
		Timeout:   10 * time.Second,
		KeepAlive: 30 * time.Second,
	}).DialContext,
	ForceAttemptHTTP2:     true,
	MaxIdleConns:          200,
	MaxIdleConnsPerHost:   100,
	IdleConnTimeout:       90 * time.Second,
	TLSHandshakeTimeout:   10 * time.Second,
	ExpectContinueTimeout: 1 * time.Second,
	ResponseHeaderTimeout: 30 * time.Second,
}

type Server struct {
	store *config.Store
	libs  atomic.Value // map[string]*config.VirtualLibrary

	coverCache *coverCache

	localIntercept LocalInterceptChain

	hookViewsRe       *regexp.Regexp
	hookLatestRe      *regexp.Regexp
	hookDetailsRe     *regexp.Regexp
	hookDetailIntroRe *regexp.Regexp
	hookImageRe       *regexp.Regexp
}

type responseHook struct {
	pattern *regexp.Regexp
	handler func(*Server, *http.Response) error
}

func (s *Server) responseHooks() []responseHook {
	return []responseHook{
		{s.hookViewsRe, (*Server).hookViews},
		{s.hookLatestRe, (*Server).hookLatest},
		{s.hookDetailsRe, (*Server).hookDetails},
		{s.hookDetailIntroRe, (*Server).hookDetailIntro},
		{s.hookImageRe, (*Server).hookImage},
	}
}

func NewServer(store *config.Store) *Server {
	s := &Server{
		store:             store,
		coverCache:        newCoverCache(),
		localIntercept:    DefaultLocalInterceptors(),
		hookViewsRe:       regexp.MustCompile(`/Users/[^/]+/Views$`),
		hookLatestRe:      regexp.MustCompile(`/Users/[^/]+/Items/Latest$`),
		hookDetailsRe:     regexp.MustCompile(`/Users/[^/]+/Items$`),
		hookDetailIntroRe: regexp.MustCompile(`/Users/[^/]+/Items/.*$`),
		hookImageRe: regexp.MustCompile(`(?i)(?:/emby)?/items/[^/]+/images/primary$`),
	}
	s.rebuildLibMap()
	return s
}

func (s *Server) rebuildLibMap() {
	cfg := s.store.Snapshot()
	m := make(map[string]*config.VirtualLibrary)
	for i := range cfg.Library {
		lib := cfg.Library[i]
		if lib.ResourceType == "rsshub" {
			continue
		}
		cp := lib
		m[lib.ID] = &cp
	}
	s.libs.Store(m)
	if s.coverCache != nil {
		s.coverCache.clearLibs()
	}
}

func (s *Server) libMap() map[string]*config.VirtualLibrary {
	v := s.libs.Load()
	if v == nil {
		return nil
	}
	return v.(map[string]*config.VirtualLibrary)
}

// RebuildLibMap reloads the in-memory virtual library map from disk config.
func (s *Server) RebuildLibMap() { s.rebuildLibMap() }

// ReloadFromDisk 重新读取 config.json 并重建虚拟库映射（代理自身的 listen/token/log 见 config/proxy.yaml）。
func (s *Server) ReloadFromDisk() error {
	if err := s.store.Load(); err != nil {
		return err
	}
	s.rebuildLibMap()
	return nil
}

func reloadAuthOK(r *http.Request, token string) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	loopback := host == "127.0.0.1" || host == "::1"
	if token == "" {
		return loopback
	}
	return r.Header.Get("X-Emby-Virtual-Lib-Reload-Token") == token
}

func (s *Server) newReverseProxy() http.Handler {
	return &httputil.ReverseProxy{
		Director: func(req *http.Request) {
			cfg := s.store.Snapshot()
			t, err := url.Parse(cfg.EmbyURL)
			if err != nil {
				return
			}
			rel := &url.URL{
				Path:     req.URL.Path,
				RawQuery: req.URL.RawQuery,
				Fragment: req.URL.Fragment,
			}
			out := t.ResolveReference(rel)
			req.URL.Scheme = out.Scheme
			req.URL.Host = out.Host
			req.URL.Path = out.Path
			req.URL.RawQuery = out.RawQuery
			req.URL.Fragment = out.Fragment
			req.Host = out.Host
			clientIP, _, _ := net.SplitHostPort(req.RemoteAddr)
			if clientIP != "" {
				prior := req.Header.Get("X-Forwarded-For")
				if prior != "" {
					req.Header.Set("X-Forwarded-For", prior+", "+clientIP)
				} else {
					req.Header.Set("X-Forwarded-For", clientIP)
				}
				req.Header.Set("X-Real-IP", clientIP)
			}
			scheme := "http"
			if req.TLS != nil {
				scheme = "https"
			}
			req.Header.Set("X-Forwarded-Protocol", scheme)
		},
		ModifyResponse: func(resp *http.Response) error {
			return s.modifyResponse(resp)
		},
		Transport:     defaultProxyTransport,
		FlushInterval: 100 * time.Millisecond,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			log.WithError(err).Warnf("upstream proxy error: %s %s", r.Method, r.URL.String())
			http.Error(w, "bad gateway", http.StatusBadGateway)
		},
	}
}

// Handler returns the root HTTP handler: internal reload route + reverse proxy to Emby.
func (s *Server) Handler(reloadToken string, afterReload func()) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /__internal/reload-config", func(w http.ResponseWriter, r *http.Request) {
		if !reloadAuthOK(r, reloadToken) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		if err := s.ReloadFromDisk(); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if afterReload != nil {
			afterReload()
		}
		log.Info("config reloaded (notify)")
		w.WriteHeader(http.StatusNoContent)
	})
	rp := s.newReverseProxy()
	mux.Handle("/", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s.localIntercept.TryAll(w, r, s) {
			return
		}
		rp.ServeHTTP(w, r)
	}))
	return mux
}

func (s *Server) modifyResponse(resp *http.Response) error {
	for _, hook := range s.responseHooks() {
		if hook.pattern.MatchString(resp.Request.URL.Path) {
			log.Debug("matched", resp.Request.URL.Path)
			log.Debug("hook", hook.pattern.String())
			return hook.handler(s, resp)
		}
	}
	return nil
}

// Listen starts the reverse proxy HTTP server.
// reloadToken: if non-empty, POST /__internal/reload-config must send header X-Emby-Virtual-Lib-Reload-Token with the same value.
// If empty, reload endpoint is only accepted from loopback (127.0.0.1 / ::1).
func (s *Server) Listen(addr string, reloadToken string, afterReload func()) error {
	log.Info("emby virtual proxy listening on ", addr)
	srv := &http.Server{
		Addr:              addr,
		Handler:           s.Handler(reloadToken, afterReload),
		ReadHeaderTimeout: 15 * time.Second,
		IdleTimeout:       120 * time.Second,
		WriteTimeout:      0, // streaming/transcode responses may run for long durations
		MaxHeaderBytes:    1 << 20,
		BaseContext: func(_ net.Listener) context.Context {
			return context.Background()
		},
	}
	return srv.ListenAndServe()
}
