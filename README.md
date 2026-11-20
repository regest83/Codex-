# Codex Project Recovery

**Codex 本地项目诊断、配置快照与恢复计划技能。** 为“项目列表突然空了”“切换账号后分组消失”“用旧备份恢复时保留新项目”等情况提供可核对的处理流程。

Portable diagnostics, project-metadata snapshots, and review-only recovery plans for Codex. The included scripts never apply a recovery plan to the live application state.

这是一个独立社区工具，与 OpenAI 官方产品没有隶属关系。它不依赖特定用户名、项目名称、项目数量或固定数据库版本；可恢复范围取决于本机仍然存在的记录及可用备份。

## 先了解它会做什么

| 能力 | 行为 |
|---|---|
| 发现本地环境 | 依次使用显式数据目录、`CODEX_HOME`、当前用户的 `.codex`；有界、非递归发现候选 |
| 只读检查 | 对照状态文件、inactive 项目快照及 SQLite 项目/目录/聊天关联，不读取聊天正文 |
| 配置快照 | 仅导出已识别的五类项目字段，不导出完整全局设置或认证凭据 |
| 恢复计划 | 按 ID 补缺、保留现有新增项目；将定义、归属、排序和主机映射冲突列出 |
| 路径迁移 | 按显式映射处理 Windows、UNC 与 POSIX 路径，遵守最长前缀和目录边界 |
| agent 工作流程 | 引导助手选择可信来源、备份、处理账号分区、验证实际恢复结果 |

**仓库没有通用的“一键覆盖 live 配置”命令。** 所有计划都标记为 `executable: false`。实际恢复需由助手根据当前应用版本、数据结构和加载行为，使用公开工具或经过核对的离线流程完成。

它不能凭空恢复没有任何记录或备份的数据。安装 skill 不会自动复制原电脑的代码目录、聊天历史、数据库或登录状态。

## 运行要求与支持程度

- Python 3.10 或以上，脚本和测试仅使用标准库，无须 `pip install`。
- 作为 skill 使用时，需要支持本地 skills 的 Codex 环境，以及用户授权访问相关本地文件。
- 独立诊断脚本不需要 GitHub 账号、API Key、联网或 Codex 插件。

| 范围 | 验证/限制 |
|---|---|
| Windows Python 脚本 | 已在真实 Windows 环境执行只读检查及本地测试 |
| Linux/macOS Python 脚本 | 仓库 CI 配置了对应测试任务，以实际 Actions 结果为准 |
| 路径语法 | 合成测试覆盖 Windows/UNC/POSIX、目录边界及父目录折叠 |
| Codex 桌面实际恢复 | 已有一次 Windows 本机离线恢复案例；2026-11-20 用户确认成功，应用工具也确认原项目重新可见；不代表其他机器或版本已验证 |
| 未来版本或未知 schema | 报告未验证/不兼容，不能猜测字段后写入 |

当前快照/计划脚本识别 [已观察的五字段布局](references/windows-state-recovery.md)。这不是官方稳定存储协议；其他布局需要根据实际证据适配。

上述实际案例使用了按本机证据准备的一次性离线程序，先备份再补回缺失配置。该程序及其个人恢复计划不在公开仓库内。公开脚本负责诊断、快照和计划；安装此 skill 后，助手仍需为使用者的当前环境确认实际写入方式。

## 安装为 Codex skill

公开仓库：[regest83/Codex-](https://github.com/regest83/Codex-)。点击仓库页面的 **Code → Download ZIP**，解压后将包含 `SKILL.md` 的文件夹重命名为 `codex-project-recovery`。也可以克隆到指定名称：

```shell
git clone https://github.com/regest83/Codex-.git codex-project-recovery
```

将这个文件夹放到以下位置：

| 环境 | 位置 |
|---|---|
| 已设置 `CODEX_HOME` | `<CODEX_HOME>/skills/codex-project-recovery/` |
| Windows 默认目录 | `%USERPROFILE%\.codex\skills\codex-project-recovery\` |
| macOS/Linux 默认目录 | `~/.codex/skills/codex-project-recovery/` |

最终结构应直接是 `codex-project-recovery/SKILL.md`，不要多套一层下载目录。若已有同名 skill，先保留旧版本，再更新本仓库的文件。新建聊天或重新加载 skills 后调用：

```text
$codex-project-recovery
我的项目列表不见了。请先检查数据是否还在，再提出保留现有项目的恢复方案。
```

也可以这样使用：

```text
使用 $codex-project-recovery 为当前项目配置生成一份独立快照。
```

```text
使用 $codex-project-recovery 对比这份旧备份与当前状态。
保留后来新增的项目和我主动修改的归属，只恢复确认缺失的部分。
```

如果只需独立运行脚本，不必安装 skill，直接在仓库中执行下面的命令。

## 独立脚本快速上手

下面的命令均从**本仓库根目录**执行。`python` 可按本机环境换为 `python3` 或 `py -3`。包含空格的路径必须加引号；`<...>` 是需要替换的参数。

### 1. 只读发现与诊断

```shell
python scripts/inspect_project_state.py
```

指定实际数据目录：

```shell
python scripts/inspect_project_state.py --codex-home "/path/to/codex-data"
```

Windows 示例：

```powershell
python scripts/inspect_project_state.py --codex-home "D:\Codex Data"
```

明确检查当前状态、某份备份与实际数据库：

```shell
python scripts/inspect_project_state.py --state "/path/current.json" --state "/path/backup.json" --db "/path/state.sqlite"
```

规则：

- 显式文件模式只检查给定的文件，不额外混入自动发现的候选。
- 自动发现默认最多读取 20 份状态候选和 20 个数据库，遗漏项会列出。需要时使用 `--max-states`、`--max-databases` 调整，范围为 1–1000。
- 多数据库逐个检查 schema；不自动选择编号最大或时间最新的文件。
- SQLite 使用只读连接并保留 WAL 可见性，不会创建不存在的数据库。
- 外来系统的路径显示存在性未知，而非“已丢失”。相对路径也不会擅自补成某个本机目录。

输出中的 `environment` 表示本次环境，`discovery` 表示候选发现情况，`states` 和 `databases` 表示每份材料的检查结果。计数为 `null` 表示无法核实，不能读作零。

### 2. 生成独立配置快照

先确保输出目录已存在，并选择一个尚不存在的文件名：

```shell
python scripts/project_snapshot.py snapshot --state "/path/source-state.json" --out "/private-backups/project-snapshot.json"
```

若项目配置在明确选定的 inactive 分区中：

```shell
python scripts/project_snapshot.py snapshot --state "/path/source-state.json" --source-owner "<已确认的来源分区键>" --out "/private-backups/project-snapshot.json"
```

不提供 `--source-owner` 时只取顶层，不会自动猜测账号。来源和当前目标应分别备份；只有旧来源备份不足以回滚当前状态。

快照是项目元数据，包含名称、目录、项目/聊天关联 ID 及来源散列，**仍属于用户数据**。它不包含完整聊天正文或项目文件，不应上传到公开仓库或 Issue。快照创建后新增的项目不会自动写进该快照；下一次运行会重新读取本机最新记录。

### 3. 生成恢复计划

```shell
python scripts/project_snapshot.py plan --snapshot "/private-backups/project-snapshot.json" --state "/path/current-state.json" --target-owner "<已确认的目标分区键>" --out "/private-backups/recovery-plan.json"
```

目录在新电脑上改变时，显式提供映射，可重复指定：

```shell
python scripts/project_snapshot.py plan --snapshot "/private-backups/project-snapshot.json" --state "/path/current-state.json" --target-owner "<已确认的目标分区键>" --path-map "C:/Projects=D:/Projects" --out "/private-backups/recovery-plan.json"
```

路径映射只调整相关元数据，不复制文件或聊天，也不登录账号。`C:/work` 不会误改 `C:/workspace`；映射参数中的 `..` 会被拒绝。来源根目录中的父目录片段会按对应平台语义规范化后进行边界匹配。

`--target-owner` 是本地状态分区索引，不是 GitHub 用户名，也不是密码。必须由实际状态和用户恢复目标确认；禁止从旧电脑的分区键推断新电脑的身份。

计划有三个结果：

| status | 含义 |
|---|---|
| `review_required` | 有可供审阅的增量，仍须核对目录、聊天 ID、schema 及实际写入流程 |
| `no_change` | 在支持的五字段比较范围内无需更改；不代表界面已经恢复 |
| `conflicts` | 存在不同定义或关联；`desired_globals` 为 `null`，不能写入 |

所有计划均为 `executable: false`，包含目标基线 SHA-256 和 24 小时有效期。目标配置发生变化、账号切换、schema 改变或计划过期后，重新生成计划。不要删除冲突标记后继续使用旧增量。

例如，旧快照有 2 个项目、当前有 7 个项目时，目标不是“回到 2 个”。相同 ID 不重复、额外的 5 个仍保留；同 ID 的目录或归属变化需明确处理，而不是用旧值覆盖。

### 4. 实际恢复与核验

遵循 [SKILL.md](SKILL.md) 选择当前应用支持的方式。某些版本在启动时读取全局配置，运行时直接改文件会被内存状态覆盖；这需要在确认加载行为后执行离线修复。不要照搬其他人的退出脚本、PID、账号键或数据库文件名。

只有重开后在应用中确认项目、目录、聊天归属及归档状态符合预期，才可称为恢复完成。JSON 读回成功、计划生成成功或测试通过都不是这一结论。

## 退出码与常见问题

| 脚本 | 返回 0 | 返回 2 |
|---|---|---|
| `inspect_project_state.py` | 本次选择的检查正常完成 | 未验证/不兼容、目录缺失或候选被截断等，需要查看 JSON |
| `project_snapshot.py` | 快照创建成功，或计划生成成功/无差异 | 冲突、未知结构、空恢复源、输出已存在或参数/文件错误 |

**项目数为零怎么办？** 先区分明确的空数据与无法验证的 `null`；再对照应用工具、其他候选快照和数据库。不能只凭一个空列表下结论。

**为什么会拒绝旧备份？** 格式不匹配、项目引用不完整、输出文件已存在、与当前记录冲突等情况都可能被拒绝。查看具体错误，不要强行把错误改成成功。

**为什么目录存在却没有聊天？** 代码目录和聊天数据库是不同的数据来源。目录还在只能证明其中现存文件存在，不能证明聊天记录完整。

**能否自动恢复所有账号？** 不会。每次只针对用户确认的目标分区，保留其他分区、认证配置、当前新聊天和原有归档状态。

**安装后没有自动每日备份？** 本仓库没有周期任务或后台常驻服务。需要备份时显式调用；周期自动化需用户另外配置。

## 验证与贡献

运行完整合成测试：

```shell
python -m unittest discover -s tests -v
```

测试在临时目录构造数据，不需要本人的 Codex 数据。覆盖动态数量、环境优先级、多数据库、未知 schema、只读约束、输出不覆盖、增量合并、冲突以及跨平台路径规则。CI 矩阵覆盖 Windows、Ubuntu、macOS 的 Python 3.10 和 3.12；是否通过以仓库 Actions 页面为准。

提交问题时请提供系统、Python 版本、脚本版本及去标识化的错误信息。不要粘贴完整全局状态、数据库、认证文件、分区键或真实聊天内容。若报告兼容问题，优先提供最小合成样例。

贡献修改前运行测试；为行为变化增加可观察的测试，不通过删除失败测试掩盖问题。保持标准库依赖与现有项目保留规则。

## 目录结构

```text
codex-project-recovery/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
│   ├── inspect_project_state.py
│   └── project_snapshot.py
├── references/
│   ├── portable-workflow.md
│   └── windows-state-recovery.md
├── tests/
└── .github/workflows/
```

## 许可证

当前暂未添加 `LICENSE` 文件，许可证由仓库所有者另行选择。
