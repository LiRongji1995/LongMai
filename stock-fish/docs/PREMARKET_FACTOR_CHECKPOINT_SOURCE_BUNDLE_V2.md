# 盘前因子 checkpoint 与独立源码 bundle V2

本工具通过新的显式脚本[build_premarket_source_bundle_v2.py](../scripts/build_premarket_source_bundle_v2.py)创建和校验独立源码目录，只为未来获准采集准备。它未启用到现有运行入口，不切换服务、locator或默认配置。正式路径32项TEST_FIXTURE检查通过；另已创建独立源码快照并完成离线入口/身份校验，未执行其真实采集。实际10月20日盘前仍因原始首见钟缺失而失败，见[部分验收](CLOSED_SESSION_PARTIAL_ACCEPTANCE_20261020.md)。

## 要解决的边界

原factor worker正常完成与父进程timeout回收partial走不同路径。已有一次真实PARTIAL记录没有RAW `first_seen_at`，原cache正确拒绝；每证券COMPLETE不能补父记录缺失的时间和完整性。V2仅为未来全新attempt构造带真实时钟的checkpoint，不修改历史原件、已有缓存、normalizer、资格、复权公式或预算。

独立源码目录同时保留准确producer字节，使未来修复不要求覆盖仍承担历史证明的旧checkout。旧cache的三个producer原路径仍是其来源引用，必须保留并继续按原路校验；新副本的路径或物理身份不能替代旧引用。创建独立bundle不提供旧checkpoint迁移或重贴新时钟的能力。

## 构建、验证与来源范围

从仓库根目录使用已验收的解释器，选择业务源码目录及全新目标目录：

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/build_premarket_source_bundle_v2.py create \
  --source-root "$BUSINESS_SOURCE_ROOT" --destination "$NEW_BUNDLE_ROOT"

"$RUNTIME_PYTHON" -I -B stock-fish/scripts/build_premarket_source_bundle_v2.py verify \
  --destination "$NEW_BUNDLE_ROOT" --manifest-sha256 "$SAVED_MANIFEST_SHA"
```

`BUSINESS_SOURCE_ROOT`指含`stockfish_core`、`scripts`及配置的业务目录。目标必须不存在且不在原源码树内；不覆盖已有目录。创建后将输出的manifest SHA另行保留，verify要求显式传入该值，不能只信任目标目录内自行声明的摘要。

创建时读取完整`stockfish_core`下的Python/JSON文件，以及固定公开清单中的入口、launcher、R1/R2/theme/delivery规范、配置、许可证与第三方说明；前后核对原源文件清单、完整SHA和七字段物理身份。配置/文档使用固定allowlist；core按完整Python/JSON范围复制，不声称自动识别其未来可能加入的私人内容。不要将凭据或真实运行数据放入这些源码文件。

新目录包含不可导入的原worker文本preimage，verify据固定原版本与精确补丁核对新worker；非worker源码、9个normalizer及其他producer保持原字节。清单采用安全相对路径，拒绝越界、额外文件、符号链接、物理迟变或不匹配；读取时核对已打开文件描述符身份。

后续verify只验证bundle本体、清单和本地preimage，不要求原可变checkout永久不变。清单中原路径与身份只证明创建时来源，不能证明旧checkout现在仍完整。文件只读权限用于防误操作，不是OS不可变保证；依然需要完整验证。工具不复制虚拟环境、DB/state或市场缓存，不自动安装依赖；部分创建失败保留现场，不做破坏性清理。

## 未来factor checkpoint合同

- 只对已固定原worker实施版本内精确补丁，写本次实际`first_seen_at`、完整`requested_codes`和每个请求代码唯一状态。
- 已完成片段保留真实`captured_at`；未尝试/未完成为UNKNOWN且没有伪造完成钟。父checkpoint及timeout仍为PARTIAL、`pagination_complete=false`。
- 已存在的factor checkpoint在worker导入SDK、登录和查询前拒绝，包括空文件、损坏文件、目录或符号链接；不续传旧行或给旧行贴新钟。
- 预期入口是现有`CaptureCache.capture_one`分配的全新UUID attempt及排他claim。工具本身不启动此入口。

范围不能扩大为任意直接`execute_request(checkpoint_dir=旧目录)`都已安全迁移：若timeout早于worker guard，未改的parent仍可能读到旧partial；它不补首见钟，原cache继续拒绝缺钟或早于新started_at的旧钟。该边界有保留反例。通用direct入口迁移需要另立合同，不能据本工具绕过。

## 实际检查与启用前要求

[正式测试文件](../tests/yaogu/test_premarket_source_bundle_v2.py)在发布目录通过32项，pytest 8.22秒、外层9.917秒、退出0。覆盖checkpoint状态与时间、旧partial提前timeout、独立verify不依赖原checkout、相对路径/精确文件集合/迟变拒绝、文件描述符竞态，以及独立解释器中的离线入口与R2/delivery身份一致。子进程在导入产品前拒绝网络和DB，没有执行真实selector或采集。

私有候选阶段另一次32项通过与正式结果分别保留；首次临时目录物理身份拒绝也保留，原因未被证明，不归咎于某个同步程序或放宽七字段断言。正式搬移只改脚本/测试位置与导入边界，原断言保持。

实际源码快照创建命令1.008秒、退出0；之后对同一bundle重新初验、分别从原源和bundle执行离线烟测、末验均退出0，续验共5.345秒。快照166件含manifest、源码3,466,049 B；离线核对18个产品模块均来自bundle，R2/delivery身份与原源一致，无额外`__pycache__`，原源与受保护文件SHA/物理身份保持。此范围是源码快照和离线入口，没有网络、业务DB、selector、真实capture或服务切换，采集进程树尚未验收。

首次私有烟测在60秒限制内未完成，失败保留；新label将监测器先匹配受禁函数名以减少无关调用开销，仍按原60秒限制和全部禁用边界通过。同期存在另一解码micro，不能归因于唯一原因或将首次改判。脱敏定位为`source-bundle-real-01-result.json`和`source-bundle-real-02-result.json`，不发布实际本机运行路径。

未来使用必须显式选择新bundle和已有合格解释器，复核实际模块均来自该目录、依赖可用、旧producer引用仍可读，再按新授权及剩余原预算执行。当前离线测试不授权网络重试，不证明冷启动、真实盘前可交付、连续交易日稳定或投资优势。现有worker/request/budget及运行服务保持。

未来另获网络授权后的启动模板如下；本轮没有执行此命令：

```sh
env PYTHONDONTWRITEBYTECODE=1 "$RUNTIME_PYTHON" -I -B \
  "$NEW_BUNDLE_ROOT/scripts/stockfish.py" premarket --config "$PRIVATE_CONFIG"
```

父解释器用`-I -B`，后代Python进程通过`PYTHONDONTWRITEBYTECODE=1`继承不写字节码的约束；仅设置父进程`sys.dont_write_bytecode`不会自动传给后代。bundle校验拒绝额外`__pycache__`，不能为运行方便放宽精确文件集合。配置和运行state仍在源码bundle之外。

许可和第三方声明随目录保留；MiroFish仍适用AGPL-3.0，不能将整个项目称作纯MIT。
