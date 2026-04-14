// Package proxy 的本地短路：在反向代理之前按链执行，不向 Emby 发请求。
package proxy

import (
	"net/http"
	"regexp"
	"strconv"
	"strings"

	log "github.com/sirupsen/logrus"
)

// LocalInterceptor 在反向代理之前执行：若匹配本机逻辑并写完响应则返回 true，不再请求 Emby。
// 新增短路时在 DefaultLocalInterceptors 中追加实现即可。
type LocalInterceptor interface {
	Name() string
	TryLocal(w http.ResponseWriter, r *http.Request, srv *Server) bool
}

// LocalInterceptChain 按顺序尝试，直到某一拦截器返回 true。
type LocalInterceptChain []LocalInterceptor

// NewLocalInterceptChain 由若干拦截器组成一条链（顺序即优先级）。
func NewLocalInterceptChain(interceptors ...LocalInterceptor) LocalInterceptChain {
	return append(LocalInterceptChain(nil), interceptors...)
}

// TryAll 依次尝试链上拦截器。
func (c LocalInterceptChain) TryAll(w http.ResponseWriter, r *http.Request, srv *Server) bool {
	for _, it := range c {
		if it == nil {
			continue
		}
		if it.TryLocal(w, r, srv) {
			log.Debug("local intercept: ", it.Name(), " ", r.Method, " ", r.URL.Path)
			return true
		}
	}
	return false
}

// DefaultLocalInterceptors 默认注册的本地短路。
// 新增规则：在此追加你的 LocalInterceptor 实现（或定义新构造函数返回 NewLocalInterceptChain(..., &mine{})）。
func DefaultLocalInterceptors() LocalInterceptChain {
	return NewLocalInterceptChain(
		&vlibPrimaryImageInterceptor{},
	)
}

// With 返回新链：保留原有顺序并在末尾追加拦截器。
func (c LocalInterceptChain) With(next ...LocalInterceptor) LocalInterceptChain {
	out := make(LocalInterceptChain, 0, len(c)+len(next))
	out = append(out, c...)
	out = append(out, next...)
	return out
}

// --- 虚拟库主封面（与 ModifyResponse hookImage 共用载荷逻辑）---

var primaryImagePathRe = regexp.MustCompile(`(?i)(?:/emby)?/items/([^/]+)/images/primary$`)

func negotiateContentEncoding(accept string) string {
	a := strings.ToLower(accept)
	for _, enc := range []string{"br", "gzip", "deflate"} {
		if strings.Contains(a, enc) {
			return enc
		}
	}
	return ""
}

func resolvePrimaryImageTag(r *http.Request) string {
	if t := r.URL.Query().Get("tag"); t != "" {
		return t
	}
	if t := r.URL.Query().Get("Tag"); t != "" {
		return t
	}
	m := primaryImagePathRe.FindStringSubmatch(r.URL.Path)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

// virtualLibraryPrimaryPayload 若为「本机虚拟库主图」请求则返回已编码正文及元数据；否则 handled=false（交给上游 Emby）。
func (s *Server) virtualLibraryPrimaryPayload(r *http.Request) (body []byte, contentType, cacheControl, contentEncoding string, handled bool, err error) {
	tag := resolvePrimaryImageTag(r)
	if tag == "" {
		return nil, "", "", "", false, nil
	}
	lib, ok := s.libMap()[tag]
	if !ok {
		return nil, "", "", "", false, nil
	}
	image, isCover, err := s.loadVirtualLibraryCover(lib)
	if err != nil {
		return nil, "", "", "", true, err
	}
	enc := negotiateContentEncoding(r.Header.Get("Accept-Encoding"))
	encoded, err := encodeBodyByContentEncoding(image, enc)
	if err != nil {
		return nil, "", "", "", true, err
	}
	cache := ""
	if isCover {
		cache = "public, max-age=86400"
	}
	return encoded, http.DetectContentType(image), cache, enc, true, nil
}

type vlibPrimaryImageInterceptor struct{}

func (*vlibPrimaryImageInterceptor) Name() string {
	return "virtual-library-primary-image"
}

func (*vlibPrimaryImageInterceptor) TryLocal(w http.ResponseWriter, r *http.Request, srv *Server) bool {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return false
	}
	body, ctype, cache, enc, handled, err := srv.virtualLibraryPrimaryPayload(r)
	if !handled {
		return false
	}
	if err != nil {
		log.Warn("local intercept virtual-library-primary-image: ", err)
		http.Error(w, "image load failed", http.StatusInternalServerError)
		return true
	}
	if cache != "" {
		w.Header().Set("Cache-Control", cache)
	}
	w.Header().Set("Content-Type", ctype)
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	if enc != "" {
		w.Header().Set("Content-Encoding", enc)
	}
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = w.Write(body)
	}
	return true
}
