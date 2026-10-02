package main

import "fmt"

func checkConfigs(h map[string]string) error {
	expected := map[string]string{"stage_aware_research_r1.json": "e2f65685814b335fcb92f192e3057da82516f442fdf94bf7ba5e6c2d89b8aa62", "stage_aware_momentum_v2.yaml": "89c03dde925256b6b9822048d4f69703c6ca60ca4a29591f4a4c1d160689ba78", "r05_strategy_experiments.yaml": "8cd729cab31f80ec84d6486c733165360ba264fc3efb2d70c0c69f5a78148462"}
	for n, v := range expected {
		if h[n] != v {
			return fmt.Errorf("FROZEN_CONFIG_DRIFT: %s", n)
		}
	}
	return nil
}
