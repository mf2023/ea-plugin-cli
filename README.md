# ea-plugin-cli — Encre Agent 插件市场统一 CLI

插件从脚手架到中央仓库上架的唯一工具链。发布后由 registry CI 把目录条目
合并进 `ea-cwh` 索引包，市场后端本地读取渲染。

```
下载即用：python build.py cli 编译出本平台的单文件可执行程序
  （build/cli/encre-plugin[.exe]，无需 pip 安装；执行时会调用
   PATH 上的宿主 Python 跑 build/venv/twine，缺组件自动补装）
也可按模块装：pip install ea-plugin-cli  # 或开发态: pip install -e cli/
```

## 命令

| 命令 | 作用 |
|---|---|
| `encre-plugin init <name>` | 生成 `ea-plugin-<name>` 脚手架（pyproject + `ea.plugins` entry point + `create_plugin` 工厂 + `ui/` 目录） |
| `encre-plugin build [pkg_dir]` | 执行中央仓库策略校验 → `python -m build` 产出 wheel/sdist → 计算每个产物的 sha256 → 写 `dist/ea-build-info.json`（目录条目草稿） |
| `encre-plugin vendor` | 官方批量发布：遍历 `core/ea_tools/*`（含 `ea-tool-`/`ea-skill-` 命名空间、放行 `system-default` tier），用同一套策略逐个打包+算摘要，把 `catalog.d/<name>.json` 和 wheel 汇总产出到 `dist/vendor/`（仅生成，不上传/不开 PR）。由 `python build.py vendor` 调用 |
| `encre-plugin verify [pkg_dir]` | 临时 venv 隔离安装 wheel，导入 entry point 工厂并回读 manifest 冒烟 |
| `encre-plugin publish [pkg_dir]` | `twine upload` 到 PyPI，随后向中央仓库提交 PR：条目写入 registry 检出的 `catalog.d/`，`git` 从 origin/main 切新分支、commit、push，`gh pr create` 开 PR |

## publish 的中央仓库联动

- `--registry-dir <目录>` 指定已有 registry 检出；不给时 CLI 自动浅克隆
  `https://github.com/mf2023/ea-cwh.git` 到本机缓存目录（`%LOCALAPPDATA%`
  / `~/.cache` 下 `encre-plugin-cli/registry`），每次强制同步到 origin/main。
- `--registry-url <git url>` 覆盖默认远端（预启用阶段可指向 fork 或本地
  bare 仓库做全流程仿真）。
- `--no-upload` 跳过 twine 上传、只走 registry PR 半边。
- `--dry-run` 只打印将上传的文件与目录条目，不做任何远端动作。
- PR 分支只携带 `catalog.d/<name>.json` 一个文件：分支切自 origin/main 后
  才写条目，工作区其它脏内容不会混进 PR。
- 过渡期提示：`verify` 的隔离 venv 需要 `encre-harness` 可解析；本地开发
  可先 `pip install -e harness/` 或加 `--no-deps` 并配 PYTHONPATH。
| `encre-plugin catalog regen` | 合并 registry `catalog.d/*.json` → 校验 → 写出 `ea-cwh` 的 `catalog.json` |
| `encre-plugin catalog check <file>` | 只校验一份 catalog.json |

## 中央仓库策略（build 阶段强制）

1. PyPI 包名必须带保留前缀 **`ea-plugin-`**（前缀命名空间已在 PyPI 注册保留）。
2. 必须声明 `[project.entry-points."ea.plugins"]`，且**一个 wheel 只允许一个插件**。
3. manifest 的 `name/version/description/author/license` 非空，`manifest.version`
   必须与 pyproject 版本一致；`tier` 必须是 `user`。
4. 必须声明 `min_ea_version` 或 `engines["encre"]`。
5. 声明了 UI（`provides_ui` / `contributes.ui`）的包，**前端必须预编译**进
   `ui/` 且由 package-data 打进 wheel——安装端永不执行构建。

## 环境变量

- PyPI 凭证：`TWINE_USERNAME` / `TWINE_PASSWORD`（CI 用 trusted publishing，不需要）。
- 自动 PR 依赖 registry 目录已 `git remote add origin` 且 `gh` 已登录。
