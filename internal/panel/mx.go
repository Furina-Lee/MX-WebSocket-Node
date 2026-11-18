package panel

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
)

var mxEndpoints = map[string]string{
	"config": http.MethodGet, "user": http.MethodGet, "alivelist": http.MethodGet,
	"push": http.MethodPost, "alive": http.MethodPost, "status": http.MethodPost,
}

// ForwardMX preserves the wire format expected by proxy-node while using the
// configured panel credentials and this client's fixed node binding.
func (c *Client) ForwardMX(ctx context.Context, w http.ResponseWriter, r *http.Request, endpoint string, body []byte) {
	method, ok := mxEndpoints[endpoint]
	if !ok || r.Method != method {
		http.NotFound(w, r)
		return
	}
	path := "/api/v1/server/UniProxy/" + endpoint
	if c.machineID > 0 {
		path = "/api/v2/server/" + endpoint
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path+"?"+c.authQuery().Encode(), bytes.NewReader(body))
	if err != nil {
		http.Error(w, "invalid panel URL", http.StatusBadGateway)
		return
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("If-None-Match", r.Header.Get("If-None-Match"))
	client := http.Client{
		Transport:     c.httpClient.Transport,
		Timeout:       c.httpClient.Timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	resp, err := client.Do(req)
	if err != nil {
		http.Error(w, "panel unavailable", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNotModified {
		http.Error(w, "panel rejected MX request", http.StatusBadGateway)
		return
	}
	if endpoint == "config" && resp.StatusCode == http.StatusOK {
		var settings map[string]json.RawMessage
		if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&settings); err != nil || settings == nil {
			http.Error(w, "invalid MX panel configuration", http.StatusBadGateway)
			return
		}
		intervals := map[string]json.RawMessage{}
		_ = json.Unmarshal(settings["base_config"], &intervals)
		if intervals == nil {
			intervals = map[string]json.RawMessage{}
		}
		for _, key := range []string{"push_interval", "pull_interval"} {
			var seconds int
			if json.Unmarshal(intervals[key], &seconds) != nil || seconds < 5 || seconds > 15 {
				intervals[key] = json.RawMessage("15")
			}
		}
		settings["base_config"], _ = json.Marshal(intervals)
		if value := resp.Header.Get("ETag"); value != "" {
			w.Header().Set("ETag", value)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(settings)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	if value := resp.Header.Get("ETag"); value != "" {
		w.Header().Set("ETag", value)
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}
