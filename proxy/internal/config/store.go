package config

import (
	"encoding/json"
	"hash/fnv"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"
)

type Store struct {
	mu      sync.RWMutex
	path    string
	cfg     *AppConfig
	modTime time.Time // mtime of config file when last loaded or saved
}

func NewStore(path string) *Store {
	return &Store{path: path, cfg: DefaultAppConfig()}
}

func (s *Store) Path() string { return s.path }

func (s *Store) Load() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	data, err := os.ReadFile(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			s.cfg = DefaultAppConfig()
			s.modTime = time.Time{}
			return nil
		}
		return err
	}
	var cfg AppConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return err
	}
	normalize(&cfg)
	s.cfg = &cfg
	if fi, err := os.Stat(s.path); err == nil {
		s.modTime = fi.ModTime()
	}
	return nil
}

// ReloadIfChanged reloads from disk when the file mtime changed (e.g. Python admin saved config).
func (s *Store) ReloadIfChanged() (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fi, err := os.Stat(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}
	if !s.modTime.IsZero() && !fi.ModTime().After(s.modTime) {
		return false, nil
	}
	data, err := os.ReadFile(s.path)
	if err != nil {
		return false, err
	}
	var cfg AppConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return false, err
	}
	normalize(&cfg)
	s.cfg = &cfg
	s.modTime = fi.ModTime()
	return true, nil
}

func normalize(cfg *AppConfig) {
	if cfg.Library == nil {
		cfg.Library = []VirtualLibrary{}
	}
	if cfg.DisplayOrder == nil {
		cfg.DisplayOrder = []string{}
	}
	if cfg.Hide == nil {
		cfg.Hide = []string{}
	}
	if cfg.AdvancedFilters == nil {
		cfg.AdvancedFilters = []AdvancedFilter{}
	}
	if cfg.LogLevel == "" {
		cfg.LogLevel = "info"
	}
	if cfg.EmbyURL == "" {
		cfg.EmbyURL = "http://127.0.0.1:8096"
	}
	for i := range cfg.Library {
		if cfg.Library[i].ID == "" {
			cfg.Library[i].ID = hashNameToID(cfg.Library[i].Name)
		}
	}
}

func (s *Store) Snapshot() *AppConfig {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneConfig(s.cfg)
}

func (s *Store) Update(mutator func(*AppConfig)) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := cloneConfig(s.cfg)
	mutator(cp)
	if err := s.saveUnlocked(cp); err != nil {
		return err
	}
	s.cfg = cp
	return nil
}

func (s *Store) Replace(cfg *AppConfig) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := cloneConfig(cfg)
	normalize(cp)
	if err := s.saveUnlocked(cp); err != nil {
		return err
	}
	s.cfg = cp
	return nil
}

func (s *Store) saveUnlocked(cfg *AppConfig) error {
	if err := os.MkdirAll(filepath.Dir(s.path), 0755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, b, 0644); err != nil {
		return err
	}
	if err := os.Rename(tmp, s.path); err != nil {
		return err
	}
	if fi, err := os.Stat(s.path); err == nil {
		s.modTime = fi.ModTime()
	}
	return nil
}

func cloneConfig(src *AppConfig) *AppConfig {
	if src == nil {
		return DefaultAppConfig()
	}
	b, err := json.Marshal(src)
	if err != nil {
		cp := *src
		return &cp
	}
	var out AppConfig
	_ = json.Unmarshal(b, &out)
	normalize(&out)
	return &out
}

func hashNameToID(name string) string {
	h := fnv.New32a()
	_, _ = h.Write([]byte(name))
	return strconv.FormatUint(uint64(h.Sum32()), 10)
}
