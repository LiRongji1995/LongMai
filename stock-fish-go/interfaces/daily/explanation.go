package daily

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

var explanationNames = map[string]string{
	"START": "启动", "ACCELERATION": "加速", "HIGH_LEVEL_REPAIR": "高位修复", "TREND_PULLBACK": "趋势回踩",
	"common": "共同条件", "liquidity": "成交额", "close_limit_down": "收盘跌停反证", "trend": "趋势", "identification": "形态识别", "behavior": "行为确认", "critical": "关键反证",
	"liquidity_bottom": "量价底线", "trend_state": "趋势状态", "setup_identification": "形态识别", "behavioral_confirmation": "行为确认", "market_compatibility": "市场情境匹配", "same_theme_context": "同题材情境", "critical_counterevidence": "关键反证",
	"amount_cny": "成交额（元）", "turnover_fraction": "换手率（比例）", "name": "名称", "selected_evidence_hashes": "被消费证据哈希", "selected_evidence_bindings": "被消费证据来源绑定",
	"sector_context": "题材情境", "capital_linkage": "资金联动", "market_status": "市场情境", "supply_pressure": "供应压力", "trend_strength": "趋势强度",
}

func explanationName(s string) string {
	if name, ok := explanationNames[s]; ok {
		return name + "（" + s + "）"
	}
	return s
}
func explanationState(s c.State) string {
	switch s {
	case c.Pass:
		return "通过（PASS）"
	case c.Fail:
		return "失败（FAIL）"
	case c.Unknown:
		return "未知（UNKNOWN）"
	case c.NotEvaluated:
		return "未评价（NOT_EVALUATED）"
	case c.NotImplemented:
		return "实现缺失（NOT_IMPLEMENTED）"
	case c.RuntimeError:
		return "运行错误（RUNTIME_ERROR）"
	}
	return "未记录"
}

func renderCondition(w io.Writer, name string, condition c.Condition, indent string) error {
	value, threshold := condition.Value.String(), condition.Threshold.String()
	if !condition.Value.Known() {
		value = "未记录/未知"
	}
	if !condition.Threshold.Known() {
		threshold = "未记录/未知"
	}
	if _, err := fmt.Fprintf(w, "%s%s：%s；实际值=%s；比较参考=%s\n", indent, explanationName(name), explanationState(condition.Status), value, threshold); err != nil {
		return err
	}
	if len(condition.Reasons) > 0 {
		if _, err := fmt.Fprintf(w, "%s  保存原因：%s\n", indent, strings.Join(condition.Reasons, ", ")); err != nil {
			return err
		}
	}
	if len(condition.Evidence) > 0 {
		if _, err := fmt.Fprintf(w, "%s  证据引用：%s\n", indent, strings.Join(condition.Evidence, ", ")); err != nil {
			return err
		}
	}
	if len(condition.Details) > 0 {
		raw, err := json.Marshal(condition.Details)
		if err != nil {
			return err
		}
		if _, err = fmt.Fprintf(w, "%s  保存的窗口/细节：%s\n", indent, raw); err != nil {
			return err
		}
	}
	keys := make([]string, 0, len(condition.Parts))
	for key := range condition.Parts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if err := renderCondition(w, key, condition.Parts[key], indent+"  "); err != nil {
			return err
		}
	}
	return nil
}

// Saved numeric JSON is emitted verbatim; decoding into float64 would lose
// exact rationals and decimal boundaries. No decision is recalculated here.
func renderSavedFacts(w io.Writer, prefix string, raw json.RawMessage, depth int) error {
	var object map[string]json.RawMessage
	if depth < 12 && json.Unmarshal(raw, &object) == nil && object != nil {
		keys := make([]string, 0, len(object))
		for key := range object {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if err := renderSavedFacts(w, prefix+"."+explanationName(key), object[key], depth+1); err != nil {
				return err
			}
		}
		return nil
	}
	_, err := fmt.Fprintf(w, "  %s = %s\n", prefix, raw)
	return err
}

func renderExplanation(w io.Writer, item c.Item, strategy string) error {
	var saved struct {
		Fact     json.RawMessage `json:"fact"`
		Decision struct {
			Candidate  string                 `json:"candidate_status"`
			Status     string                 `json:"decision_status"`
			Common     c.Condition            `json:"common"`
			Setups     map[string]c.Condition `json:"setups"`
			Stages     map[string]c.Condition `json:"stage_candidates"`
			Conditions json.RawMessage        `json:"conditions"`
			Matched    []string               `json:"matched_setups"`
			Reasons    []string               `json:"reason_codes"`
			Evidence   []string               `json:"evidence_refs"`
			Background json.RawMessage        `json:"background"`
		} `json:"decision"`
	}
	if err := json.Unmarshal(item.Payload, &saved); err != nil {
		return err
	}
	status := saved.Decision.Candidate
	if status == "" {
		status = saved.Decision.Status
	}
	conclusion := map[string]string{"CORE_PASS": "至少一个完整形态通过核心条件，属于研究候选。", "CORE_FAIL": "核心条件已知失败，未入选。", "CORE_UNKNOWN": "核心资料不足，不能当作已知失败。", "FOCUS": "保存的 V2 条件给出焦点观察状态。", "WATCH": "保存的 V2 条件给出继续观察状态。", "REJECTED": "保存的 V2 条件给出拒绝状态。", "UNKNOWN": "保存资料不足，最终状态未知。"}[status]
	if conclusion == "" {
		conclusion = "该历史结果未保存可识别的最终状态。"
	}
	if _, err := fmt.Fprintf(w, "中文解释（仅解释保存结果，不重新选股）\n结论：%s 保存状态=%s\n全部命中形态：%s\n", conclusion, status, strings.Join(saved.Decision.Matched, ", ")); err != nil {
		return err
	}
	if len(saved.Decision.Reasons) > 0 {
		if _, err := fmt.Fprintf(w, "决策原因：%s\n", strings.Join(saved.Decision.Reasons, ", ")); err != nil {
			return err
		}
	}
	if saved.Decision.Common.Status != "" {
		if err := renderCondition(w, "common", saved.Decision.Common, ""); err != nil {
			return err
		}
	}
	setups := saved.Decision.Setups
	if len(setups) == 0 {
		setups = saved.Decision.Stages
	}
	for _, stage := range []string{"START", "ACCELERATION", "HIGH_LEVEL_REPAIR", "TREND_PULLBACK"} {
		condition, ok := setups[stage]
		if !ok {
			if _, err := fmt.Fprintf(w, "%s：未记录该形态条件\n", explanationName(stage)); err != nil {
				return err
			}
			continue
		}
		if err := renderCondition(w, stage, condition, ""); err != nil {
			return err
		}
	}
	if len(saved.Decision.Conditions) > 0 {
		var condition c.Condition
		var object map[string]json.RawMessage
		if err := json.Unmarshal(saved.Decision.Conditions, &object); err != nil {
			return err
		}
		if _, ok := object["status"]; ok {
			if err := json.Unmarshal(saved.Decision.Conditions, &condition); err != nil {
				return err
			}
			if err := renderCondition(w, "conditions", condition, ""); err != nil {
				return err
			}
		} else {
			keys := make([]string, 0, len(object))
			for key := range object {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			for _, key := range keys {
				if err := json.Unmarshal(object[key], &condition); err != nil {
					return err
				}
				if err := renderCondition(w, key, condition, ""); err != nil {
					return err
				}
				condition = c.Condition{}
			}
		}
	}
	if _, err := io.WriteString(w, "保存事实、交易会话窗口与证据（缺失字段不推断；JSON 保留精确数值）：\n"); err != nil {
		return err
	}
	if len(saved.Fact) > 0 {
		if err := renderSavedFacts(w, "fact", saved.Fact, 0); err != nil {
			return err
		}
	}
	if len(saved.Decision.Background) > 0 && string(saved.Decision.Background) != "null" {
		label := "背景标签与核心形态分开报告；R1 不把完整背景统一作为核心否决条件。"
		if strategy == c.V2 {
			label = "V2 背景情境及其决策作用以保存的条件树为准。"
		}
		if _, err := fmt.Fprintln(w, label); err != nil {
			return err
		}
		if err := renderSavedFacts(w, "background", saved.Decision.Background, 0); err != nil {
			return err
		}
	}
	if len(saved.Decision.Evidence) > 0 {
		if _, err := fmt.Fprintf(w, "决策证据引用：%s\n", strings.Join(saved.Decision.Evidence, ", ")); err != nil {
			return err
		}
	}
	_, err := io.WriteString(w, "实际值、比较方式或会话窗口未保存时标为未记录；原始明细可用 --format json 查看。\n")
	return err
}
