package config

// Types mirror https://github.com/pipi20xx/emby-virtual-proxy/blob/main/src/models.py
// so the bundled Vue admin can read/write config.json without schema drift.

type AdvancedFilterRule struct {
	Field         string  `json:"field"`
	Operator      string  `json:"operator"`
	Value         *string `json:"value,omitempty"`
	RelativeDays  *int    `json:"relative_days,omitempty"`
}

type AdvancedFilter struct {
	ID       string               `json:"id"`
	Name     string               `json:"name"`
	MatchAll bool                 `json:"match_all"`
	Rules    []AdvancedFilterRule `json:"rules"`
}

type VirtualLibrary struct {
	ID                    string  `json:"id"`
	Name                  string  `json:"name"`
	ResourceType          string  `json:"resource_type"`
	ResourceID            string  `json:"resource_id,omitempty"`
	Image                 string  `json:"image,omitempty"` // Go-only: static thumb file path
	ImageTag              *string `json:"image_tag,omitempty"`
	AdvancedFilterID      *string `json:"advanced_filter_id,omitempty"`
	Order                   int     `json:"order"`
	SourceLibrary         string  `json:"source_library,omitempty"`
	Conditions            any     `json:"conditions,omitempty"`
	CoverCustomZhFontPath string  `json:"cover_custom_zh_font_path,omitempty"`
	CoverCustomEnFontPath string  `json:"cover_custom_en_font_path,omitempty"`
	CoverCustomImagePath  string  `json:"cover_custom_image_path,omitempty"`
}

func (l *VirtualLibrary) NeedRecursive() bool {
	switch l.ResourceType {
	case "collection":
		return false
	default:
		return true
	}
}

func (l *VirtualLibrary) GetParamKey() string {
	switch l.ResourceType {
	case "collection":
		return "ParentId"
	case "tag":
		return "TagIds"
	case "genre":
		return "GenreIds"
	case "studio":
		return "StudioIds"
	case "person":
		return "PersonIds"
	default:
		return ""
	}
}

type AppConfig struct {
	EmbyURL              string            `json:"emby_url"`
	EmbyAPIKey           string            `json:"emby_api_key"`
	LogLevel             string            `json:"log_level"`
	DisplayOrder         []string          `json:"display_order"`
	Hide                 []string          `json:"hide"`
	Library              []VirtualLibrary  `json:"library"`
	AdvancedFilters      []AdvancedFilter  `json:"advanced_filters"`
	EnableCache          bool              `json:"enable_cache"`
	DefaultCoverStyle    string            `json:"default_cover_style"`
	CustomZhFontPath     string            `json:"custom_zh_font_path"`
	CustomEnFontPath     string            `json:"custom_en_font_path"`
	CustomImagePath      string            `json:"custom_image_path"`
}

func DefaultAppConfig() *AppConfig {
	return &AppConfig{
		EmbyURL:            "http://127.0.0.1:8096",
		LogLevel:           "info",
		DisplayOrder:       []string{},
		Hide:               []string{},
		Library:            []VirtualLibrary{},
		AdvancedFilters:    []AdvancedFilter{},
		EnableCache:        true,
		DefaultCoverStyle: "style_multi_1",
	}
}

func (c *AppConfig) OrderedLibraries() []VirtualLibrary {
	if len(c.Library) == 0 {
		return nil
	}
	byID := make(map[string]VirtualLibrary, len(c.Library))
	for _, lib := range c.Library {
		byID[lib.ID] = lib
	}
	seen := make(map[string]bool)
	out := make([]VirtualLibrary, 0, len(c.Library))
	for _, id := range c.DisplayOrder {
		if lib, ok := byID[id]; ok {
			out = append(out, lib)
			seen[id] = true
		}
	}
	for _, lib := range c.Library {
		if !seen[lib.ID] {
			out = append(out, lib)
		}
	}
	return out
}
