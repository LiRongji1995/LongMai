> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# 人工验收：NOT_RUN_BY_USER

本机真实数据路径和实际已核验 run/证券 ID 在本轮私有交接 MANUAL_ACCEPTANCE.md；包不含私有数据库或个人绝对路径。下列步骤使用用户选择的同一个配置。输出文件选择新文件，禁止覆盖。

1. 解压后运行 `./stockfish --version`、`./stockfish --help`，核对 BUILD.json。
2. `./stockfish init --db 已有已发布库 --config 新配置`，然后 `./stockfish doctor --config 新配置`；只读/本地研究/在线刷新分别看。
3. `./stockfish picks --config 新配置 --strategy r1`；用交接实际 ID 做 `picks --run-id ID`，核对历史标记、观察日、cutoff、完整候选及覆盖。
4. 用同一 run 的证券 `explain 证券ID --run-id ID`；分别导出 JSON、CSV、Markdown 到新路径；核对完整数量、条件及未知。
5. `daily --strategy r1 --dry-run`，确认计划后显式执行 `daily --strategy r1`。缺资料时应明确阻塞。refresh 未配置应失败，不假成功。此步真实研究额度由操作者另行安排。
6. 退出终端并新开进程重复 picks/explain，核对同一 run；重复查询不能创建新作业。

合成演示必须标 TEST_FIXTURE。用户本人尚未执行，因此本文件不替用户标 PASS。生产未激活，完整规模旧库增量仍独立 BLOCKED。
