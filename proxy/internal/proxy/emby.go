package proxy

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strings"

	"emby-virtual-lib/proxy/internal/config"

	log "github.com/sirupsen/logrus"
)

func getUserID(req *http.Request) string {
	path := req.URL.Path
	parts := strings.Split(path, "/")
	userID := ""
	if len(parts) > 1 && parts[1] == "emby" {
		if len(parts) > 3 {
			userID = parts[3]
		}
	} else {
		if len(parts) > 2 {
			userID = parts[2]
		}
	}
	return userID
}

func embyURL(embyBase, path, userID string) string {
	return embyBase + strings.Replace(path, "{userId}", userID, 1)
}

func setXEmbyParams(query, originalQuery url.Values, headers, originalHeaders http.Header) {
	xEmbyKeys := []string{
		"X-Emby-Client", "X-Emby-Device-Name", "X-Emby-Device-Id",
		"X-Emby-Client-Version", "X-Emby-Token", "X-Emby-Language", "X-Emby-Authorization",
	}
	for _, key := range xEmbyKeys {
		if val := originalQuery.Get(key); val != "" {
			query.Set(key, val)
		}
		if headerVal := originalHeaders.Get(key); headerVal != "" {
			headers.Set(key, headerVal)
		}
	}
}

func doGetJSON(baseURL string, query url.Values, headers http.Header, cookies []*http.Cookie) (map[string]interface{}, error) {
	client := &http.Client{}
	req, err := http.NewRequest(http.MethodGet, baseURL, nil)
	if err != nil {
		return nil, err
	}
	if query != nil {
		req.URL.RawQuery = query.Encode()
	}
	for k, v := range headers {
		for _, vv := range v {
			req.Header.Add(k, vv)
		}
	}
	for _, c := range cookies {
		req.AddCookie(c)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var data map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return nil, err
	}
	return data, nil
}

func (s *Server) getItems(lib *config.VirtualLibrary, originalReq *http.Request, extQuery url.Values) map[string]interface{} {
	originalQuery := originalReq.URL.Query()
	query := url.Values{}
	if key := lib.GetParamKey(); key != "" && lib.ResourceID != "" {
		query.Set(key, lib.ResourceID)
	}
	log.Debug("getItems query ", query)
	query.Set("IncludeItemTypes", "Movie,Series,Video,Game,MusicAlbum,Episode")
	query.Set("ImageTypeLimit", originalQuery.Get("ImageTypeLimit"))
	query.Set("Fields", originalQuery.Get("Fields"))
	query.Set("EnableTotalRecordCount", originalQuery.Get("EnableTotalRecordCount"))
	if originalQuery.Get("Filters") != "" {
		query.Set("Filters", originalQuery.Get("Filters"))
	}
	if lib.NeedRecursive() {
		query.Set("Recursive", "true")
	}
	if extQuery != nil {
		for k, v := range extQuery {
			query.Set(k, v[0])
		}
	} else {
		query.Set("SortBy", originalQuery.Get("SortBy"))
		query.Set("SortOrder", originalQuery.Get("SortOrder"))
	}
	headers := http.Header{}
	setXEmbyParams(query, originalReq.URL.Query(), headers, originalReq.Header)
	log.Debug("getItems query after setXEmbyParams ", query)
	headers.Set("Accept-Language", originalReq.Header.Get("Accept-Language"))
	headers.Set("User-Agent", originalReq.Header.Get("User-Agent"))
	headers.Set("accept", "application/json")
	userID := getUserID(originalReq)
	urlStr := embyURL(s.store.Snapshot().EmbyURL, "/emby/Users/{userId}/Items", userID)
	data, err := doGetJSON(urlStr, query, headers, originalReq.Cookies())
	if err != nil {
		return nil
	}
	items, ok := data["Items"].([]interface{})
	if !ok {
		log.Debug("getCollectionData data count", 0)
	} else {
		log.Debug("getCollectionData data count", len(items))
	}
	return data
}
