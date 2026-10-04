// Package daily composes saved queries and the existing application service.
package daily

import (
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"os"
	"path/filepath"
)

const Protocol = "STOCKFISH_DAILY_V1"

type Config struct {
	Version          string            `json:"version"`
	Database         string            `json:"database"`
	InstanceRoot     string            `json:"instance_root,omitempty"`
	Artifacts        string            `json:"artifacts"`
	Mode             string            `json:"mode"`
	Strategy         string            `json:"preferred_strategy"`
	Budget           int               `json:"budget_seconds"`
	Identity         map[string]string `json:"database_identity"`
	DatabasePathHash string            `json:"database_path_sha256"`
	ServerURL        string            `json:"server_url,omitempty"`
	TokenFile        string            `json:"token_file,omitempty"`
	Provider         string            `json:"provider,omitempty"`
	Python           string            `json:"python,omitempty"`
	Worker           string            `json:"worker,omitempty"`
	WorkerHash       string            `json:"worker_sha256,omitempty"`
}

func configPath(explicit string) (string, error) {
	if explicit == "" {
		explicit = os.Getenv("STOCKFISH_CONFIG")
	}
	if explicit == "" {
		root, e := os.UserConfigDir()
		if e != nil {
			return "", e
		}
		explicit = filepath.Join(root, "stockfish", "config.json")
	}
	return filepath.Abs(explicit)
}
func anchored(base, path string) string {
	if path == "" || filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(base, path)
}
func load(path string) (Config, error) {
	var conf Config
	raw, e := os.ReadFile(path)
	if e != nil {
		return conf, e
	}
	if len(raw) > 64<<10 {
		return conf, errors.New("CONFIG_SIZE_LIMIT")
	}
	if e = json.Unmarshal(raw, &conf); e != nil {
		return conf, e
	}
	if conf.Version != Protocol || conf.Budget < 1 || conf.Budget > 1800 || (conf.Mode != "embedded" && conf.Mode != "server") {
		return conf, errors.New("INVALID_DAILY_CONFIG")
	}
	base := filepath.Dir(path)
	conf.Database = anchored(base, conf.Database)
	conf.InstanceRoot = anchored(base, conf.InstanceRoot)
	conf.Artifacts = anchored(base, conf.Artifacts)
	conf.TokenFile = anchored(base, conf.TokenFile)
	conf.Python = anchored(base, conf.Python)
	conf.Worker = anchored(base, conf.Worker)
	return conf, nil
}
func strategy(value string) (string, error) {
	switch value {
	case "r1", c.R1:
		return c.R1, nil
	case "v2", c.V2:
		return c.V2, nil
	}
	return "", errors.New("STRATEGY_MUST_BE_R1_OR_V2")
}
func createConfig(path string, conf Config) error {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return e
	}
	f, e := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	e = json.NewEncoder(f).Encode(conf)
	if e == nil {
		e = f.Sync()
	}
	return errors.Join(e, f.Close())
}
