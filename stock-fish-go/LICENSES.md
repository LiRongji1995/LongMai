# 依赖许可与工具链来源

Go工具链来自 https://go.dev/dl/ 的官方JSON清单，私有安装版本1.27.1 darwin/amd64。下载包SHA256：`8f8f52c6649542cf027bbc9b9c68d1ec042f9f34808a40413f0b8b3f66f3caa4`，本机计算一致。工具链自身遵循BSD-3-Clause，不随本模块分发。

SQLite驱动固定modernc.org/sqlite v1.36.3，许可证BSD-3-Clause，已核对该版本官方源码LICENSE（https://raw.githubusercontent.com/modernc-org/sqlite/v1.36.3/LICENSE）。不声称为最新版本。SQLite本体公共领域说明见 https://www.sqlite.org/copyright.html 。

以下是实际编译依赖的原始许可文件副本；依赖源码版本以go.sum校验。

|模块|版本|许可文本|
|---|---|---|
|github.com/dustin/go-humanize|v1.0.1|[原文](licenses/github.com_dustin_go-humanize-v1.0.1.txt)|
|github.com/google/uuid|v1.6.0|[原文](licenses/github.com_google_uuid-v1.6.0.txt)|
|github.com/mattn/go-isatty|v0.0.20|[原文](licenses/github.com_mattn_go-isatty-v0.0.20.txt)|
|github.com/ncruces/go-strftime|v0.1.9|[原文](licenses/github.com_ncruces_go-strftime-v0.1.9.txt)|
|github.com/remyoudompheng/bigfft|v0.0.0-20230129092748-24d4a6f8daec|[原文](licenses/github.com_remyoudompheng_bigfft-v0.0.0-20230129092748-24d4a6f8daec.txt)|
|golang.org/x/exp|v0.0.0-20230315142452-642cacee5cc0|[原文](licenses/golang.org_x_exp-v0.0.0-20230315142452-642cacee5cc0.txt)|
|golang.org/x/sys|v0.31.0|[原文](licenses/golang.org_x_sys-v0.31.0.txt)|
|modernc.org/libc|v1.61.13|[原文](licenses/modernc.org_libc-v1.61.13.txt)|
|modernc.org/mathutil|v1.7.1|[原文](licenses/modernc.org_mathutil-v1.7.1.txt)|
|modernc.org/memory|v1.8.2|[原文](licenses/modernc.org_memory-v1.8.2.txt)|
|modernc.org/sqlite|v1.36.3|[原文](licenses/modernc.org_sqlite-v1.36.3.txt)|
