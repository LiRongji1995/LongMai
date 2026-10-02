package architecture_test

import (
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// Parse all build-tag variants, including Windows, without invoking any runtime
// entry point. The core package is a one-way compatibility adapter only.
func TestDependencyDirection(t *testing.T) {
	const prefix = "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/"
	root := ".."
	e := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if d.Name() == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if strings.HasPrefix(rel, "core/") || strings.HasPrefix(rel, "cmd/stockfish-r1/") {
			return nil
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if err != nil {
			return err
		}
		for _, spec := range file.Imports {
			name, err := strconv.Unquote(spec.Path.Value)
			if err != nil {
				return err
			}
			target := strings.TrimPrefix(name, prefix)
			if name == prefix+"core" {
				t.Errorf("%s imports compatibility core; depend on the owning layer", rel)
			}
			pure := strings.HasPrefix(rel, "domain/") || strings.HasPrefix(rel, "evidence/") || strings.HasPrefix(rel, "facts/") || strings.HasPrefix(rel, "strategies/")
			if pure && (name == "database/sql" || name == "net/http" || name == "os/exec" || name == "os" || strings.HasPrefix(target, "infrastructure/") || strings.HasPrefix(target, "interfaces/") || strings.HasPrefix(target, "application/")) {
				t.Errorf("pure layer %s imports runtime dependency %s", rel, name)
			}
			if strings.HasPrefix(rel, "domain/") && strings.HasPrefix(name, prefix) && !strings.HasPrefix(target, "domain/") {
				t.Errorf("domain %s imports outer layer %s", rel, name)
			}
			if strings.HasPrefix(rel, "ports/") && strings.HasPrefix(name, prefix) && !strings.HasPrefix(target, "domain/") && target != "facts" && !strings.HasPrefix(target, "facts/") {
				t.Errorf("port %s imports adapter %s", rel, name)
			}
			if strings.HasPrefix(rel, "application/") && (strings.HasPrefix(target, "interfaces/") || strings.HasPrefix(target, "infrastructure/") || name == "database/sql" || name == "net/http" || name == "os/exec") {
				t.Errorf("application %s imports concrete adapter %s", rel, name)
			}
			if strings.HasPrefix(rel, "infrastructure/") && strings.HasPrefix(target, "interfaces/") {
				t.Errorf("infrastructure %s imports transport %s", rel, name)
			}
		}
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
}
