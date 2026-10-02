package cli

import (
	"context"
	"io"
	"os"
	"path/filepath"
)

// Publish an entirely written and synced export with an atomic no-replace link.
// A cancelled stream never leaves a final filename that resembles a complete run.
func atomicExport(ctx context.Context, dest string, write func(io.Writer) error) error {
	f, e := os.CreateTemp(filepath.Dir(dest), ".stockfish-export-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if e = write(f); e != nil {
		return e
	}
	if e = f.Sync(); e != nil {
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	if e = ctx.Err(); e != nil {
		return e
	}
	return os.Link(f.Name(), dest)
}
