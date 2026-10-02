package cli

import (
	"context"
	"errors"
	"flag"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"io"
)

func isolation(ctx context.Context, args []string, out, log io.Writer) error {
	if len(args) == 0 {
		return errors.New("isolation init|preflight|cutover|export-changes|verify-export|rollback|restore")
	}
	f := flag.NewFlagSet("isolation "+args[0], flag.ContinueOnError)
	f.SetOutput(log)
	root := f.String("root", "", "explicit private isolated root")
	source := f.String("source", "", "frozen source inside root")
	target := f.String("target", "", "V2 target inside root")
	listen := f.String("listen", "127.0.0.1:18081", "independent loopback address")
	export := f.String("export", "", "saved change export manifest")
	if e := f.Parse(args[1:]); e != nil {
		return e
	}
	if *root == "" || f.NArg() != 0 {
		return errors.New("ISOLATED_ROOT_REQUIRED")
	}
	switch args[0] {
	case "init":
		v, e := lifecycle.Init(ctx, *root, *source, *target, *listen)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "preflight":
		v, e := lifecycle.Preflight(ctx, *root)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "cutover":
		v, e := lifecycle.Cutover(ctx, *root)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "export-changes":
		v, e := lifecycle.ExportChanges(ctx, *root)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "verify-export":
		v, e := lifecycle.VerifyExport(ctx, *root, *export)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "rollback":
		v, e := lifecycle.Rollback(ctx, *root)
		if er := emit(out, v); er != nil {
			return er
		}
		return e
	case "restore":
		v, e := lifecycle.Restore(ctx, *root, *export)
		if e != nil {
			return e
		}
		return emit(out, v)
	default:
		return errors.New("UNKNOWN_ISOLATION_COMMAND")
	}
}
