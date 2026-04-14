package config

import (
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

// ProxySettings 来自本地 YAML，仅描述代理进程自身（监听、日志、重载接口鉴权等），与 config.json 中的 Emby/虚拟库配置分离。
type ProxySettings struct {
	Listen      string `yaml:"listen"`
	LogLevel    string `yaml:"log_level"`
	ReloadToken string `yaml:"reload_token"`
}

type proxyYAMLFile struct {
	Listen      string `yaml:"listen"`
	LogLevel    string `yaml:"log_level"`
	ReloadToken string `yaml:"reload_token"`
}

// DefaultProxySettings 在缺少 proxy.yaml 或字段为空时使用。
func DefaultProxySettings() *ProxySettings {
	return &ProxySettings{
		Listen:   ":8000",
		LogLevel: "info",
	}
}

// LoadProxySettings 读取 YAML；文件不存在时返回默认设置且不报错。
func LoadProxySettings(path string) (*ProxySettings, error) {
	out := DefaultProxySettings()
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return out, nil
		}
		return nil, err
	}
	var raw proxyYAMLFile
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	if s := strings.TrimSpace(raw.Listen); s != "" {
		out.Listen = s
	}
	if s := strings.TrimSpace(raw.LogLevel); s != "" {
		out.LogLevel = s
	}
	out.ReloadToken = strings.TrimSpace(raw.ReloadToken)
	return out, nil
}
