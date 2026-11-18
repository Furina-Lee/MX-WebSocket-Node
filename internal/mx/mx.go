package mx

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/cedar2025/xboard-node/internal/config"
	"github.com/cedar2025/xboard-node/internal/nlog"
	"github.com/cedar2025/xboard-node/internal/panel"
)

func Run(ctx context.Context, cfg *config.Config, client *panel.Client) error {
	binary := os.Getenv("XBOARD_MX_BINARY")
	if binary == "" {
		binary = "/usr/local/bin/mx-proxy-node"
	}
	if !filepath.IsAbs(binary) {
		return errors.New("XBOARD_MX_BINARY must be an absolute path")
	}
	if _, err := exec.LookPath(binary); err != nil {
		return fmt.Errorf("MX executable: %w", err)
	}

	dir, err := filepath.Abs(filepath.Join(cfg.Kernel.ConfigDir, "mx-"+strconv.Itoa(cfg.Panel.NodeID)))
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer listener.Close()

	var secret [32]byte
	rand.Read(secret[:])
	token := hex.EncodeToString(secret[:])
	restart := make(chan struct{}, 1)
	relay := &http.Server{
		Handler:           handler(client, token, strconv.Itoa(cfg.Panel.NodeID), restart),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 35 * time.Second,
	}
	go func() { _ = relay.Serve(listener) }()
	defer relay.Close()

	cert := map[string]string{
		"mode": "self", "domain": cfg.Cert.Domain,
		"cert_file": filepath.Join(dir, "cert.pem"),
		"key_file":  filepath.Join(dir, "key.pem"),
	}
	if cert["domain"] == "" {
		cert["domain"] = "localhost"
	}
	if cfg.Cert.CertFile != "" && cfg.Cert.KeyFile != "" {
		if _, certErr := os.Stat(cfg.Cert.CertFile); certErr == nil {
			if _, keyErr := os.Stat(cfg.Cert.KeyFile); keyErr == nil {
				cert["mode"] = "file"
				cert["cert_file"], err = filepath.Abs(cfg.Cert.CertFile)
				if err != nil {
					return err
				}
				cert["key_file"], err = filepath.Abs(cfg.Cert.KeyFile)
				if err != nil {
					return err
				}
			}
		}
	}
	settings := map[string]any{
		"log_level": "info",
		"panel": map[string]any{
			"api_host": "http://" + listener.Addr().String(), "api_key": token,
			"node_id": cfg.Panel.NodeID, "node_type": "mx",
			"push_interval": 15, "pull_interval": 15,
		},
		"mundoproxy": map[string]any{
			"asset_path": dir, "listen_ip": "0.0.0.0", "report_min_traffic_kb": 1,
		},
		"cert": cert,
	}
	file, err := os.CreateTemp(dir, "proxy-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	encodeErr := json.NewEncoder(file).Encode(settings)
	closeErr := file.Close()
	if err := errors.Join(encodeErr, closeErr); err != nil {
		return err
	}

	for ctx.Err() == nil {
		workerCtx, stopWorker := context.WithCancel(ctx)
		cmd := exec.CommandContext(workerCtx, binary, "-c", file.Name())
		cmd.Dir = dir
		// Keep machine and panel credentials out of the worker environment.
		cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "HOME=" + dir, "TZ=" + os.Getenv("TZ")}
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
		cmd.WaitDelay = 8 * time.Second
		err := cmd.Start()
		if err == nil {
			done := make(chan error, 1)
			go func() { done <- cmd.Wait() }()
			select {
			case <-restart:
				nlog.Core().Info("restarting MX worker to close revoked user sessions", "node_id", cfg.Panel.NodeID)
				stopWorker()
				<-done
				// Removals seen during graceful shutdown are included when
				// the new worker starts with a fresh panel user list.
				select {
				case <-restart:
				default:
				}
				continue
			case err = <-done:
			}
		}
		stopWorker()
		if ctx.Err() != nil {
			break
		}
		nlog.Core().Warn("MX worker exited, restarting", "node_id", cfg.Panel.NodeID, "error", err)
		select {
		case <-ctx.Done():
		case <-time.After(5 * time.Second):
		}
	}
	return nil
}

func handler(client *panel.Client, token, nodeID string, restart chan<- struct{}) http.Handler {
	var usersMu sync.Mutex
	var previous map[int]string
	observeUsers := func(users []panel.User) {
		current := make(map[int]string, len(users))
		for _, user := range users {
			current[user.ID] = user.UUID
		}
		for id, uuid := range previous {
			if current[id] != uuid {
				select {
				case restart <- struct{}{}:
				default:
				}
				break
			}
		}
		previous = current
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		query := r.URL.Query()
		if subtle.ConstantTimeCompare([]byte(query.Get("token")), []byte(token)) != 1 || query.Get("node_id") != nodeID {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		var endpoint string
		for _, prefix := range []string{"/api/v1/server/UniProxy/", "/api/v2/server/"} {
			if suffix, ok := strings.CutPrefix(r.URL.Path, prefix); ok {
				endpoint = suffix
				break
			}
		}
		var body []byte
		if r.Method == http.MethodPost {
			raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 2<<20))
			if err != nil {
				http.Error(w, "invalid report size", http.StatusBadRequest)
				return
			}
			var fields map[string]json.RawMessage
			if json.Unmarshal(raw, &fields) != nil || fields == nil {
				http.Error(w, "invalid report", http.StatusBadRequest)
				return
			}
			for _, key := range []string{"token", "node_id", "node_type", "machine_id"} {
				delete(fields, key)
			}
			body, _ = json.Marshal(fields)
		}
		if endpoint == "user" && r.Method == http.MethodGet {
			// Keep concurrent snapshots ordered so an older response cannot
			// recreate the baseline after a removal.
			usersMu.Lock()
			defer usersMu.Unlock()
		}
		client.ForwardMX(r.Context(), w, r, endpoint, body, observeUsers)
	})
}
