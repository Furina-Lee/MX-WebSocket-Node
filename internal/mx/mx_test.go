package mx

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/cedar2025/xboard-node/internal/config"
	"github.com/cedar2025/xboard-node/internal/panel"
)

func TestRelayUsesFixedMachineBinding(t *testing.T) {
	var calls int
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		q := r.URL.Query()
		if q.Get("token") != "panel-token" || q.Get("machine_id") != "3" || q.Get("node_id") != "7" {
			t.Error("wrong panel credentials or node binding")
		}
		switch r.URL.Path {
		case "/api/v2/server/config":
			w.Header().Set("ETag", "config-tag")
			w.Write([]byte(`{"protocol":"mx","networkSettings":{},"base_config":{"pull_interval":60,"push_interval":120}}`))
		case "/api/v2/server/push":
			var traffic map[string][2]int64
			if err := json.NewDecoder(r.Body).Decode(&traffic); err != nil || len(traffic) != 1 || traffic["12"] != [2]int64{10, 20} {
				t.Error("the accepted report did not preserve bytes or remove untrusted auth fields")
			}
			w.Write([]byte(`{"data":true}`))
		default:
			t.Error("unexpected endpoint: " + r.URL.Path)
		}
	}))
	defer upstream.Close()
	relay := handler(panel.NewClient(config.PanelConfig{URL: upstream.URL, Token: "panel-token", MachineID: 3, NodeID: 7}), "local-token", "7", nil)

	send := func(method, path, body string) *httptest.ResponseRecorder {
		t.Helper()
		w := httptest.NewRecorder()
		relay.ServeHTTP(w, httptest.NewRequest(method, path, strings.NewReader(body)))
		return w
	}
	if w := send("GET", "/api/v1/server/UniProxy/config?token=local-token&node_id=9", ""); w.Code != 401 {
		t.Fatal("relay accepted a different node")
	}
	if w := send("GET", "/api/v1/server/UniProxy/config?token=wrong&node_id=7", ""); w.Code != 401 {
		t.Fatal("relay accepted an invalid token")
	}
	if w := send("GET", "/api/v1/server/UniProxy/config?token=local-token&node_id=7&machine_id=99", ""); w.Code != 200 ||
		w.Header().Get("ETag") != "config-tag" || !strings.Contains(w.Body.String(), `"push_interval":15`) ||
		!strings.Contains(w.Body.String(), `"networkSettings":{}`) {
		t.Fatalf("bad config reply: %d %s", w.Code, w.Body.String())
	}
	if w := send("POST", "/api/v1/server/UniProxy/push?token=local-token&node_id=7", `{"node_id":99,"12":[10,20]}`); w.Code != 200 {
		t.Fatalf("bad report reply: %d", w.Code)
	}
	if calls != 2 {
		t.Fatalf("upstream calls = %d, want 2", calls)
	}
}

func TestRelayRestartsOnlyForAuthoritativeRevocations(t *testing.T) {
	var response string
	var code int
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(code)
		_, _ = w.Write([]byte(response))
	}))
	defer upstream.Close()
	restarts := make(chan struct{}, 1)
	relay := handler(panel.NewClient(config.PanelConfig{URL: upstream.URL, NodeID: 7}), "local-token", "7", restarts)

	for _, tc := range []struct {
		payload     string
		input, want int
		restart     bool
	}{
		{`{"users":[{"id":1,"uuid":"first"},{"id":2,"uuid":"second"}]}`, 200, 200, false},
		{``, 304, 304, false},
		{`{}`, 500, 502, false},
		{`{"users":null}`, 200, 502, false},
		{`{"users":[{"id":1}]}`, 200, 502, false},
		{`{"users":[{"id":2,"uuid":"second"}]}`, 200, 200, true},
		{`{"users":[{"id":2,"uuid":"second"}]}`, 200, 200, false},
		{`{"users":[{"id":2,"uuid":"new-key"}]}`, 200, 200, true},
		{`{"users":[]}`, 200, 200, true},
		{`{"users":[]}`, 200, 200, false},
		{`{"users":[{"id":3,"uuid":"third"}]}`, 200, 200, false},
	} {
		response, code = tc.payload, tc.input
		w := httptest.NewRecorder()
		relay.ServeHTTP(w, httptest.NewRequest("GET", "/api/v1/server/UniProxy/user?token=local-token&node_id=7", nil))
		if w.Code != tc.want {
			t.Fatalf("payload %s got status %d, want %d", tc.payload, w.Code, tc.want)
		}
		restarted := false
		select {
		case <-restarts:
			restarted = true
		default:
		}
		if restarted != tc.restart {
			t.Fatalf("payload %s restart = %t, want %t", tc.payload, restarted, tc.restart)
		}
	}
}
