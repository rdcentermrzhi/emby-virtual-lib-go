package main

import (
	"flag"
	"os"
	"path/filepath"
	"strings"

	"emby-virtual-lib/proxy/internal/config"
	"emby-virtual-lib/proxy/internal/proxy"

	log "github.com/sirupsen/logrus"
)

func findRepoRoot() (string, error) {
	wd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	dir := wd
	for range 10 {
		st, err := os.Stat(filepath.Join(dir, "admin", "admin_server.py"))
		if err == nil && !st.IsDir() {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return wd, nil
}

func main() {
	log.SetFormatter(&log.TextFormatter{FullTimestamp: true})

	configPath := flag.String("config", filepath.Join("config", "config.json"), "path to config.json (virtual libraries / Emby)")
	proxyConfigPath := flag.String("proxy-config", filepath.Join("config", "proxy.yaml"), "path to proxy-only YAML (listen, log_level, reload_token)")
	flag.Parse()

	root, err := findRepoRoot()
	if err != nil {
		log.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		log.Fatal(err)
	}

	proxySettings, err := config.LoadProxySettings(*proxyConfigPath)
	if err != nil {
		log.Fatal("proxy config: ", err)
	}
	applyLogLevel(proxySettings.LogLevel)

	store := config.NewStore(*configPath)
	if err := store.Load(); err != nil {
		log.Fatal(err)
	}

	token := strings.TrimSpace(os.Getenv("EMBY_PROXY_RELOAD_TOKEN"))
	if token == "" {
		token = strings.TrimSpace(proxySettings.ReloadToken)
	}

	px := proxy.NewServer(store)

	proxyYAML := *proxyConfigPath
	afterReload := func() {
		ps, err := config.LoadProxySettings(proxyYAML)
		if err != nil {
			log.Warn("reload proxy yaml: ", err)
			return
		}
		applyLogLevel(ps.LogLevel)
	}

	if err := px.Listen(proxySettings.Listen, token, afterReload); err != nil {
		log.Fatal("proxy: ", err)
	}
}

func applyLogLevel(level string) {
	switch strings.ToLower(strings.TrimSpace(level)) {
	case "debug":
		log.SetLevel(log.DebugLevel)
	case "warn":
		log.SetLevel(log.WarnLevel)
	case "error":
		log.SetLevel(log.ErrorLevel)
	default:
		log.SetLevel(log.InfoLevel)
	}
}
