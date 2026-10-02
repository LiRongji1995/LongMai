# 验证记录

本记录只描述本轮实际检查；不是完整 R1 迁移等价或投资验证。

## 基线与源码边界

- 起始本地 HEAD 与只读核验远端 main：`2e256883f1103eecb0014161c3de54b181890124`；分支 `review`，起始无 dirty。
- 新增 `stock-fish-go/`；仅在项目综述补充隔离实现状态、根第三方通知补充依赖许可索引。原 Python 策略、数据库 schema/内容、服务、发布目录均未修改。
- 默认冻结配置的原字节 SHA256 固定在 cmd/stockfish-r1/config_identity.go；构建时对全部 Go 文件、go.mod、go.sum 计算独立源码身份，运行再记录二进制 SHA256。

## 实际命令与检查范围

下表 `GO` 指本轮私有工具链 `work/r1-go-tools/go/bin/go`，`ORACLE` 指被忽略目录 `reports/codex/go-r1-migration/TEST_FIXTURE-oracle-v2`。完整日志保存在 `reports/codex/go-r1-migration/`。

|命令|结果|范围|
|---|---|---|
|`work/r1-go-tools/go/bin/gofmt -w stock-fish-go`|退出0|Go格式化|
|`go vet ./...`（模块内）|退出0|静态检查|
|`STOCKFISH_R1_ORACLE=$ORACLE go test -v ./...`|退出0|15个顶层测试，其中1项包含5组原始fixture差分|
|`STOCKFISH_R1_ORACLE=$ORACLE go test -race ./...`|退出0|同范围race；不代表Windows原生或全市场race|
|`python tools/build.py --go … --output …`|退出0|构建、固定源码身份|
|`GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build …`|退出0|仅交叉编译，未原生运行|
|`python -m pytest -q stock-fish/tests/r05_product/test_research_r1.py -k 'amount_boundary or ma20_boundaries or accel_or or accel_required_conditions or accel_known_fail_beats_missing or independent_setup_background_turnover_and_order or close_limit_safety'`|退出0，23 passed、31 deselected|有意选取R1定向回归，不是全仓测试|
|Go CLI fixture `run` / `verify`|run预期退出3；verify退出0|仅迁移诊断，正式研究未完成|

Python oracle生成5组真实源码构造的TEST_FIXTURE：repair、weakening、pullback、low_amount、missing_price；每组8只，共40个逐股案例。Go从相同原始输入独立验证来源、选修订并计算，比较资格池/完整性、精确收益、精确市场RS、当前与T-5 MA20、收盘跌停、公共及四形态状态、全部命中、CORE集合，以及每股选中的原始bar哈希。未删除差异样本、未用浮点容差。

独立工程测试包括：小数精确阈值/半偶舍入、三态、未实现独立状态、未来资料、缺父来源、PARTIAL、允许/不允许历史重建证明、修订冲突、重复JSON键、缺会话、SQL递归长查询实际取消、纯MA计算取消、source/output字节超限、原库只读、payload/索引损坏、释放事务、取消无成功标记、篡改输出拒绝。

## 未执行或尚未满足

完整市场/题材/资金/供应背景、全部理由与来源差分、完整Python模型输入合同、所有数值/默认值canonical向量、完整JSON阶段细粒度取消、Windows原生资源保护与发布未完成。无持久/磁盘窗口缓存，因此没有缓存淘汰验收。未跑Qlib、执行回测、十二组消融或全仓测试。工程测试通过不能把这些未完成项变成PASS。

## 真实运行及性能

授权额度最多2次/3600s；每次1800s，未新增Python全池参考运行。复用的Python保存run为 `r1_c8b5bcad861cb2b4deba15f161c91b3c6346306e44237dbc2c2b0ec0dab64918`，本轮已用原验证器核验manifest、completion及全部共享引用/哈希。

第一次Go全池诊断在318.59s协作取消（1532只已处理），原因是自审发现历史读取260会话与旧315会话合同不一致；保存failure，候选UNAVAILABLE。随后恢复原315会话范围，新增fixture全部选用证据哈希差分。该次不能作为最终代码验收或性能基准。

最终运行数据与差异清单见私有 `reports/codex/go-r1-migration/`。完整研发状态必须继续标为PARTIAL；即使诊断全池算完且检查字段一致，也不能宣称正式R1研究完成。尚无同范围、同环境、受控的完整新旧性能基准，不能把历史1390.64s与本轮诊断耗时相除计算加速。

最终同输入全池诊断：3040只，615.57s，采样峰值RSS 600.625MiB，退出3。独立verify退出0。与已保存Python结果检查资格/公共/四形态/命中/CORE的3040只逐股对照，0处差异；未检查字段仍保留阻塞。诊断分区6 PASS、2993 FAIL、41 UNKNOWN，不发布有效候选。合计真实额度934.16s、2次，次数已用完。最终源码hash `1beb2844dc2b099ad51dbc87a99c9069105454ef00e64f10df234618606df95c`。完整性能边界见私有交付报告。
