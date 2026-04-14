package proxy

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"emby-virtual-lib/proxy/internal/config"

	log "github.com/sirupsen/logrus"
)

// loadVirtualLibraryCover 读取虚拟库封面（内存缓存）；占位图全局只读一次磁盘。
func (s *Server) loadVirtualLibraryCover(lib *config.VirtualLibrary) (data []byte, fromDiskCover bool, err error) {
	if s.coverCache == nil {
		return legacyLoadVirtualLibraryCover(lib)
	}
	return s.coverCache.get(lib)
}

// legacyLoadVirtualLibraryCover 无缓存实例时的回退（测试或异常构造 Server）。
func legacyLoadVirtualLibraryCover(lib *config.VirtualLibrary) ([]byte, bool, error) {
	data, _, found := readLibCoverFromDisk(lib)
	if found {
		return data, true, nil
	}
	ph := filepath.Join("assets", "placeholder.png")
	b, e := os.ReadFile(ph)
	if e != nil {
		return nil, false, e
	}
	return b, false, nil
}

func (s *Server) hookImage(resp *http.Response) error {
	log.Debug("hookImage")
	encodedBody, contentType, cache, encoding, handled, err := s.virtualLibraryPrimaryPayload(resp.Request)
	if !handled {
		return nil
	}
	if err != nil {
		return err
	}
	if cache != "" {
		resp.Header.Set("Cache-Control", cache)
	}
	resp.Body = io.NopCloser(bytes.NewReader(encodedBody))
	resp.ContentLength = int64(len(encodedBody))
	resp.Header.Set("Content-Length", strconv.Itoa(len(encodedBody)))
	resp.Header.Set("Content-Type", contentType)
	if encoding == "" {
		resp.Header.Del("Content-Encoding")
	} else {
		resp.Header.Set("Content-Encoding", encoding)
	}
	resp.StatusCode = 200
	resp.Status = "200 OK"
	return nil
}

func (s *Server) hookDetailIntro(resp *http.Response) error {
	const template = `{
    "Name": "Sample Library",
    "ServerId": "",
    "Id": "1241",
    "Guid": "470c3d1e3b5e4a0287ad485a5cf67207",
    "Etag": "8281abb37d32a2b95db7e5a5df4407a4",
    "DateCreated": "2025-04-19T09:07:17.0000000Z",
    "CanDelete": false,
    "CanDownload": false,
    "PresentationUniqueKey": "470c3d1e3b5e4a0287ad485a5cf67207",
    "SupportsSync": true,
    "SortName": "Sample Library",
    "ForcedSortName": "Sample Library",
    "ExternalUrls": [],
    "Taglines": [],
    "RemoteTrailers": [],
    "ProviderIds": {},
    "IsFolder": true,
    "ParentId": "1",
    "Type": "CollectionFolder",
    "UserData": {
        "PlaybackPositionTicks": 0,
        "IsFavorite": false,
        "Played": false
    },
    "ChildCount": 1,
    "DisplayPreferencesId": "470c3d1e3b5e4a0287ad485a5cf67207",
    "PrimaryImageAspectRatio": 1.7777777777777777,
    "CollectionType": "tvshows",
    "ImageTags": {
        "Primary": "79219cbf328f6dfc6e2b3ad599233d34"
    },
    "BackdropImageTags": [],
    "LockedFields": [],
    "LockData": false,
    "Subviews": [
        "series",
        "studios",
        "genres",
        "episodes",
        "series",
        "folders"
    ]
}`
	components := strings.Split(resp.Request.URL.Path, "/")
	id := components[len(components)-1]
	lib, ok := s.libMap()[id]
	if !ok {
		return nil
	}
	log.Debug("hookDetailIntro id", id)
	var data map[string]interface{}
	if err := json.Unmarshal([]byte(template), &data); err != nil {
		return err
	}
	data["Name"] = lib.Name
	data["Id"] = id
	data["ImageTags"] = map[string]string{"Primary": id}
	bodyBytes, err := json.Marshal(data)
	if err != nil {
		return err
	}
	encoding := resp.Header.Get("Content-Encoding")
	encodedBody, err := encodeBodyByContentEncoding(bodyBytes, encoding)
	if err != nil {
		return err
	}
	resp.Body = io.NopCloser(bytes.NewReader(encodedBody))
	resp.ContentLength = int64(len(encodedBody))
	resp.Header.Set("Content-Type", "application/json")
	resp.Header.Set("Content-Length", strconv.Itoa(len(encodedBody)))
	if encoding == "" {
		resp.Header.Del("Content-Encoding")
	} else {
		resp.Header.Set("Content-Encoding", encoding)
	}
	resp.StatusCode = 200
	resp.Status = "200 OK"
	return nil
}

func (s *Server) hookDetails(resp *http.Response) error {
	log.Debug("hookDetails")
	parentID := resp.Request.URL.Query().Get("ParentId")
	lib, ok := s.libMap()[parentID]
	if !ok {
		hasID := false
		for key := range resp.Request.URL.Query() {
			if strings.HasSuffix(key, "Id") {
				hasID = true
				break
			}
		}
		if !hasID {
			return s.hookViews(resp)
		}
		return nil
	}
	bodyText := s.getItems(lib, resp.Request, nil)
	if bodyText == nil {
		return nil
	}
	bodyBytes, err := json.Marshal(bodyText)
	if err != nil {
		return err
	}
	return s.writeJSONBody(resp, bodyBytes)
}

func (s *Server) writeJSONBody(resp *http.Response, body []byte) error {
	encoding := resp.Header.Get("Content-Encoding")
	encodedBody, err := encodeBodyByContentEncoding(body, encoding)
	if err != nil {
		return err
	}
	resp.Body = io.NopCloser(bytes.NewReader(encodedBody))
	resp.ContentLength = int64(len(encodedBody))
	resp.Header.Set("Content-Type", "application/json")
	resp.Header.Set("Content-Length", strconv.Itoa(len(encodedBody)))
	if encoding == "" {
		resp.Header.Del("Content-Encoding")
	} else {
		resp.Header.Set("Content-Encoding", encoding)
	}
	return nil
}

func (s *Server) hookLatest(resp *http.Response) error {
	log.Debug("hookLatest")
	start := time.Now()
	parentID := resp.Request.URL.Query().Get("ParentId")
	lib, ok := s.libMap()[parentID]
	if !ok {
		return nil
	}
	q := url.Values{}
	q.Set("SortBy", "DateLastContentAdded,DateCreated,SortName")
	q.Set("SortOrder", "Descending")
	q.Set("Limit", resp.Request.URL.Query().Get("Limit"))
	q.Set("IsPlayed", "false")
	if lib.NeedRecursive() {
		q.Set("Recursive", "true")
	}
	log.Debug("before getCollectionData")
	getDataStart := time.Now()
	itemsData := s.getItems(lib, resp.Request, q)
	if itemsData == nil {
		return nil
	}
	rawItems, ok := itemsData["Items"].([]interface{})
	if !ok {
		rawItems = []interface{}{}
	}
	items := rawItems
	log.Debugf("getCollectionData done, cost: %v, items: %d", time.Since(getDataStart), len(items))
	marshalStart := time.Now()
	bodyBytes, err := json.Marshal(items)
	log.Debugf("json.Marshal done, cost: %v", time.Since(marshalStart))
	if err != nil {
		return err
	}
	encoding := resp.Header.Get("Content-Encoding")
	encodedBody, err := encodeBodyByContentEncoding(bodyBytes, encoding)
	if err != nil {
		return err
	}
	resp.Body = io.NopCloser(bytes.NewReader(encodedBody))
	resp.ContentLength = int64(len(encodedBody))
	resp.Header.Set("Content-Length", strconv.Itoa(len(encodedBody)))
	if encoding == "" {
		resp.Header.Del("Content-Encoding")
	} else {
		resp.Header.Set("Content-Encoding", encoding)
	}
	log.Debugf("hookLatest total cost: %v", time.Since(start))
	return nil
}

func (s *Server) hookViews(resp *http.Response) error {
	const template = `{
		"BackdropImageTags": [],
		"CanDelete": false,
		"CanDownload": false,
		"ChildCount": 1,
		"CollectionType": "tvshows",
		"DateCreated": "2025-04-19T09:07:17.0000000Z",
		"DisplayPreferencesId": "470c3d1e3b5e4a0287ad485a5cf67207",
		"Etag": "8281abb37d32a2b95db7e5a5df4407a4",
		"ExternalUrls": [],
		"ForcedSortName": "Sample Library",
		"Guid": "470c3d1e3b5e4a0287ad485a5cf67207",
		"Id": "1241",
		"ImageTags": {
			"Primary": "79219cbf328f6dfc6e2b3ad599233d34"
		},
		"IsFolder": true,
		"LockData": false,
		"LockedFields": [],
		"Name": "Sample Library",
		"ParentId": "1",
		"PresentationUniqueKey": "470c3d1e3b5e4a0287ad485a5cf67207",
		"PrimaryImageAspectRatio": 1.7777777777777777,
		"ProviderIds": {},
		"RemoteTrailers": [],
		"ServerId": "",
		"SortName": "Sample Library",
		"Taglines": [],
		"Type": "CollectionFolder",
		"UserData": {
			"IsFavorite": false,
			"PlaybackPositionTicks": 0,
			"Played": false
		}
	}`
	log.Debug("hookViews")
	log.Debug("resp.Header.Get(Content-Encoding)", resp.Header.Get("Content-Encoding"))
	bodyBytes, err := decodeResponseBody(resp.Header.Get("Content-Encoding"), resp.Body)
	if err != nil {
		return err
	}
	var data map[string]interface{}
	if err := json.Unmarshal(bodyBytes, &data); err != nil {
		log.Warn("json.Unmarshal error", err)
		resp.Body = io.NopCloser(bytes.NewReader(bodyBytes))
		return nil
	}
	items, ok := data["Items"].([]interface{})
	if !ok {
		items = []interface{}{}
	}
	if len(items) == 0 {
		return nil
	}
	typedItems := make([]map[string]interface{}, 0)
	for _, item := range items {
		typedItems = append(typedItems, item.(map[string]interface{}))
	}
	serverID := typedItems[0]["ServerId"].(string)
	log.Debug("Items count ", len(typedItems))
	cfg := s.store.Snapshot()
	newItems := make([]map[string]interface{}, 0)
	for _, lib := range cfg.OrderedLibraries() {
		if lib.ResourceType == "rsshub" {
			continue
		}
		var item map[string]interface{}
		if err := json.Unmarshal([]byte(template), &item); err != nil {
			continue
		}
		item["Name"] = lib.Name
		item["SortName"] = lib.Name
		item["ForcedSortName"] = lib.Name
		item["Id"] = lib.ID
		item["ImageTags"] = map[string]string{"Primary": lib.ID}
		item["ServerId"] = serverID
		newItems = append(newItems, item)
	}
	if len(cfg.Hide) != 0 {
		if slices.Contains(cfg.Hide, "all") {
			typedItems = []map[string]interface{}{}
		} else {
			oldItems := []map[string]interface{}{}
			for _, item := range typedItems {
				if slices.Contains(cfg.Hide, item["CollectionType"].(string)) {
					continue
				}
				oldItems = append(oldItems, item)
			}
			typedItems = oldItems
		}
	}
	typedItems = append(newItems, typedItems...)
	log.Debug("new view items count ", len(typedItems))
	data["Items"] = typedItems
	newBody, err := json.Marshal(data)
	if err != nil {
		return err
	}
	encoding := resp.Header.Get("Content-Encoding")
	encodedBody, err := encodeBodyByContentEncoding(newBody, encoding)
	if err != nil {
		return err
	}
	resp.Body = io.NopCloser(bytes.NewReader(encodedBody))
	resp.ContentLength = int64(len(encodedBody))
	resp.Header.Set("Content-Length", strconv.Itoa(len(encodedBody)))
	if encoding == "" {
		resp.Header.Del("Content-Encoding")
	} else {
		resp.Header.Set("Content-Encoding", encoding)
	}
	return nil
}
