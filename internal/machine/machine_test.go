package machine

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/cedar2025/xboard-node/internal/config"
	"github.com/cedar2025/xboard-node/internal/panel"
)

func TestRediscoverReplacesNodeWhenProtocolChanges(t *testing.T) {
	for _, pair := range [][2]string{{"vless", "mx"}, {"mx", "vless"}, {"vless", "trojan"}} {
		t.Run(pair[0]+"_to_"+pair[1], func(t *testing.T) {
			testRediscoverProtocolChange(t, pair[0], pair[1])
		})
	}
}

func testRediscoverProtocolChange(t *testing.T, from, to string) {
	t.Helper()
	updated := panel.MachineNode{ID: 7, Type: to}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v2/server/machine/nodes" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(panel.MachineNodesResponse{
			Nodes: []panel.MachineNode{updated},
		})
	}))
	defer server.Close()

	dir := t.TempDir()
	worker := filepath.Join(dir, "mx-proxy-node")
	if err := os.WriteFile(worker, []byte("#!/bin/sh\nexit 0\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("XBOARD_MX_BINARY", worker)
	cfg := &config.Config{
		Panel:   config.PanelConfig{URL: server.URL},
		Machine: &config.MachineConfig{MachineID: 3},
	}
	cfg.Kernel.ConfigDir = dir
	o := New(cfg)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	addr := listener.Addr().String()
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	oldCtx, cancelOld := context.WithCancel(ctx)
	old := &nodeHandle{cancel: cancelOld, done: make(chan struct{}), nodeType: from}
	o.nodes[updated.ID] = old
	go func() {
		<-oldCtx.Done()
		_ = listener.Close()
		close(old.done)
	}()
	defer o.stopAll()

	o.rediscover(ctx)
	current := o.nodes[updated.ID]
	if current == nil || current == old || current.nodeType != to {
		t.Fatalf("expected the same node ID to run %s, got %+v", to, current)
	}
	select {
	case <-old.done:
	default:
		t.Fatal("replacement was scheduled before the old service finished stopping")
	}
	rebound, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatalf("old port still held: %v", err)
	}
	defer rebound.Close()

	o.rediscover(ctx)
	if o.nodes[updated.ID] != current {
		t.Fatal("unchanged protocol caused a second restart")
	}
}
