# 保存 R2 完整清单交付 V2

`SAVED_R2_COMPLETE_DELIVERY_V2` / `SAVED_R2_COMPLETE_DELIVERY_PROJECTION_V2` 是显式离线协调入口的新版本。它沿用 [V1 历史合同](SAVED_R2_COMPLETE_DELIVERY_V1.md)的严格原 run、完整全池、原输入和来源消费合同，新增一个限定的交付边界：新派生展示档案在读取期间发生真实文件状态变化时，原展示器仍拒绝该解释；在再次验证原来源与实现身份后，协调器交付完整原模型观察集合，解释保持 UNKNOWN。

V1 合同及其实际源版本、保存运行和失败记录保留为历史材料，不回写为成功。本版本不改 R1/R2 选择公式、资格、原逐股行、顺序、数量、score、FOCUS 或原 input/run cutoff，也不改变默认服务、`DAILY_V1` 或 `STAGE_AWARE_MOMENTUM_V2`。初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；工程交付不证明投资优势或连续调度可靠。

本实现登记 `source_revision=2`，版本及 feature 名称仍为 V2。先前 revision 1 候选的三份源码/合同原字节、46 项工程检查及真实协调中断记录保持不变；该候选未获真实协调验收，不能以其工程检查宣称交付成功。

## 入口与不变合同

公开函数仍是 `stockfish_core.yaogu.saved_r2_delivery.deliver_saved_r2`，CLI 仍是显式 `deliver-saved-r2`：

```sh
python scripts/codex_research.py deliver-saved-r2 \
  --run SAVED_FULL_R2_JSON \
  --run-manifest SAVED_RUN_MANIFEST_JSON \
  --input EXACT_ORIGINAL_INPUT_JSON \
  --sources-manifest REGISTERED_ORIGINAL_SOURCES_JSON \
  --context-cutoff AWARE_CONTEXT_TIME \
  --membership-mode DATED_ONLY \
  --output NEW_PRIVATE_DIRECTORY
```

完整 run 和真实原 `MarketInput` 必须通过整文件 SHA、canonical digest、全部 eligible IID/name、原完整计数、选择标志、选择顺序及逐项全文核验。`SAVED_R2_DELIVERY_RUN_MANIFEST_V1` 与 `SAVED_R2_DELIVERY_SOURCES_V1` 的严格输入 schema 保持；这两个 V1 是输入声明格式，与本次协调实现版本不同。保存 JSON、path map、自填 hash 或普通 lambda 不恢复来源、selector 或 native parent 能力。所有登记原三件、原实际四时钟和 whole RAW 必须经原消费者认证；当前 cohort 不能倒填历史成员。

所有原来源、身份、内容 hash、schema、时钟、全池或逐项关系错误继续硬失败。合法缺题材父、合法 PARTIAL/不完整分页或晚到资料按原合同逐轴 UNKNOWN，不提升父状态。原 run 在 `as_of` 尚不可见时，仍只输出不含候选 ID、数量或数值的状态 metadata，不生成原候选导出或新解释。

## 新派生档案与可交付文件分开

新目录先保存原 run 的正式 `all.csv`、`observation.csv`、`focus.csv`、`unselected.csv` 和 `observation.json`。新题材分组、native theme、来源审计、经济附件及归档展示器的中间文件先写入 `NON_DELIVERY_AUDIT/`，保持其真实 pinned 路径，不移动、覆写或重打时间。

只有原归档展示器完整验真通过且新解释在实际 `as_of` 可见，才把新解释的字节或已验对象投影到顶层可交付文件：`grouped.json`、`economic-direction-explanation.json`、可选 `native-theme-context.json` 和 `display/`。正常通过仍交付完整概念角色、类内原梯度及经济解释；原业务关系、供应商范围、未知全方向强度和晚到时钟边界不改变。

展示器成功返回后，协调器仍按其实际返回的 `files` SHA 与字节数重新读取完整 JSON/TXT，要求磁盘文件范围恰好对应两份声明文件和 manifest，再将 manifest 的文件声明、状态和消费档案与真实返回值/JSON 交叉核对；TXT 必须与已验 JSON 的完整 text 字节一致。该登记在交付终态再核。成功返回后发生的 TXT 改写、替换、额外文件或声明冲突属于硬失败，不进入下述 fallback，不使用自行重算 hash 作为 producer 凭证。

revision 2 另将 `consumed_archives.explanation` 与 `grouped_parent` 逐份绑定本次实际写入的 RegisteredFile：resolved path、写入 SHA 与已写完整字节长度必须精确一致。manifest 和 display JSON 互相一致不足以替代这项绑定；附件首次读取前自洽改写金额并重算 content hash，或同时改写 grouped 父及附件绑定，也必须在精确 fallback catch 外硬失败，不发布任何新解释值。

`NON_DELIVERY_AUDIT/audit-manifest.json` 明确记录写入字节身份与失败角色。它的 write hash 不是当前来源 authority，也不表示发生变化的派生文件现已通过验证。该目录不进入最终 `files` 交付索引；不得将其内容作为可用题材、业务值或候选评分显示。

## 唯一新增 fallback

fallback 只围住原归档展示器调用，精确接受 `ValueError("ECONOMIC_DISPLAY_ARCHIVE_CHANGED")`。该错误表示新派生档案前后 FP5 或读取长度校验拒绝；保留原 `st_ctime_ns` 等守卫。不得把错误当成普通 read-atime、必然 iCloud 变动或合法新数值修订。

触发后先再次重读全部原 registry，并复核本次实现 identity。任何原源或代码变化仍失败。两项都通过时：

- `status=COMPLETE_SAVED_OBSERVATION_MODEL_FALLBACK`。
- `new_explanation_status=UNKNOWN_FAILED_ARCHIVE_STATE`，`economic_explanation_delivery_succeeded=false`。
- `display_failure` 保留真实错误。producer 已调用次数不归零，不将失败记为正常经济方向交付。
- 使用原选择行及原 `primary_stage` 构造 `ORIGINAL_SAVED_MODEL_ONLY` 分组，完整 IID 并集、唯一主归属、逐行全文和类内原顺序保持。
- 顶层 `grouped.json`、TXT、返回值和最终交付索引不包含新的题材或业务值；失败的新档案留在明确的 NON_DELIVERY_AUDIT 目录。

SHA 不符、档案 content hash 不符、schema/绑定/钟不合法、原源变化及其他 `ValueError` 不走该分支，也不 blanket catch。新解释只是尚不可见时保留原模型交付的既有分支；真实空观察集合与缺结果仍分别处理。UNKNOWN 不补零，不删除无解释个股，不截前十，不把原 UNKNOWN 全池当推荐候选。

## 资源与验收

字节守卫保持：单 input/run 与单派生文件 256 MiB、单原件 8 MiB、唯一来源总 2 GiB、全部派生输出合计 512 MiB。NON_DELIVERY_AUDIT 中的所有字节和成功后的顶层投影均纳入同一派生总预算。OS 峰值与墙钟仍须由真实调用方单独登记、观测，不以 fixture 用量宣称硬 OS 限制。

隔离 `TEST_FIXTURE` 在真实派生文件读中追加字节，实际触发未修改的原展示器 FP5 拒绝，再核完整原行、hash、顺序、数量和解释不泄露；同时覆盖真实原源变化仍失败、真实档案 content hash/schema 破坏仍失败、实现身份变化仍失败及正常完整解释仍可交付。另在 genuine 展示器实际成功写文件并返回后，真实追加/替换 TXT、加入未声明文件或改写 manifest，验证其全部硬失败。测试不调用网络、SDK、数据库或 selector，不以 mock 成功或降低断言替代真实守卫。

revision 2 的反例实际改写原展示器首次读取前的附件金额，或自洽改写 grouped 父及附件关联；genuine 归档展示器完成真实文件读取并成功返回后，协调器仍按本次原写入身份拒绝。该检查不从改写后的自 hash 恢复来源权能。

真实 V1 协调失败、独立归档展示续接、V2 工程测试、后续真实 V2 协调验收分别记账。一次归档续接只验证保存文件及展示关系，不重新认证 source registry、selector、历史 PIT 或完整周期。V2 尚未通过真实完整协调消费时应明确未验收，不将原 V1 失败改称 V2 成功。
