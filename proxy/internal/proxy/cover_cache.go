package proxy

import (
	"os"
	"path/filepath"
	"sync"

	"emby-virtual-lib/proxy/internal/config"
)

// coverCache 缓存虚拟库封面与占位图，避免每次请求读盘。
// 配置重载后应调用 clearLibs()：虚拟库列表或封面可能已变；占位图路径不变则保留内存中的占位字节。
type coverCache struct {
	mu sync.RWMutex

	byLibID map[string]coverEntry

	placeholder []byte // 全局共享，只从磁盘读一次
}

type coverEntry struct {
	data     []byte
	fromDisk bool
}

func newCoverCache() *coverCache {
	return &coverCache{
		byLibID: make(map[string]coverEntry),
	}
}

func (c *coverCache) clearLibs() {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.byLibID = make(map[string]coverEntry)
}

// readLibCoverFromDisk 仅从磁盘解析封面（不含占位图回退）。
func readLibCoverFromDisk(lib *config.VirtualLibrary) (data []byte, fromDisk bool, found bool) {
	if lib.Image != "" {
		b, e := os.ReadFile(lib.Image)
		if e == nil {
			return b, true, true
		}
	}
	for _, p := range []string{
		filepath.Join("images", lib.ID+".jpg"),
		filepath.Join("images", lib.ID+".png"),
		filepath.Join("images", lib.ID+".gif"),
		filepath.Join("images", lib.ID+".webp"),
		filepath.Join("images", lib.Name+".jpg"),
		filepath.Join("images", lib.Name+".png"),
		filepath.Join("images", lib.Name+".gif"),
		filepath.Join("images", lib.Name+".webp"),
	} {
		b, e := os.ReadFile(p)
		if e == nil {
			return b, true, true
		}
	}
	return nil, false, false
}

func (c *coverCache) placeholderBytes() ([]byte, error) {
	if len(c.placeholder) > 0 {
		return c.placeholder, nil
	}
	ph := filepath.Join("assets", "placeholder.png")
	b, err := os.ReadFile(ph)
	if err != nil {
		return nil, err
	}
	c.placeholder = b
	return c.placeholder, nil
}

// get 返回封面字节及是否来自真实封面文件；未命中磁盘时使用已缓存的占位图。
func (c *coverCache) get(lib *config.VirtualLibrary) (data []byte, fromDisk bool, err error) {
	c.mu.RLock()
	if e, ok := c.byLibID[lib.ID]; ok {
		c.mu.RUnlock()
		return e.data, e.fromDisk, nil
	}
	c.mu.RUnlock()

	c.mu.Lock()
	defer c.mu.Unlock()
	if e, ok := c.byLibID[lib.ID]; ok {
		return e.data, e.fromDisk, nil
	}

	raw, disk, found := readLibCoverFromDisk(lib)
	if found {
		owned := make([]byte, len(raw))
		copy(owned, raw)
		c.byLibID[lib.ID] = coverEntry{data: owned, fromDisk: disk}
		return owned, disk, nil
	}

	ph, err := c.placeholderBytes()
	if err != nil {
		return nil, false, err
	}
	c.byLibID[lib.ID] = coverEntry{data: ph, fromDisk: false}
	return ph, false, nil
}
