# 可移植使用与恢复计划

## 带到另一台电脑

分享包含 `SKILL.md`、`agents`、`scripts`、`references` 的 `codex-project-recovery` 文件夹。安装到对方实际的 `<CODEX_HOME>/skills/`；没有配置 `CODEX_HOME` 时放在对方用户目录的 `.codex/skills/`。不要带入自己的全局状态、数据库、认证文件、项目快照、恢复计划或日志。

在目标电脑调用 `$codex-project-recovery` 后重新发现当地环境。skill 工具本身不携带原电脑的数据；恢复原电脑历史需要另外取得用户授权的项目文件与相应记录或备份。迁移普通项目入口、迁移聊天历史、复制代码目录是三件不同的操作，不互相冒充。

## 每次发现当前状态

诊断脚本无参数时按实际数据目录发现候选。它不会读认证文件或递归扫描整个用户目录。需要检查外置盘备份时，直接用重复的 `--state` 指定；可用 `--db` 指定实际数据库。选择范围可以来自用户给定路径、应用项目工具或已确认的本机设置，不能为了“自动”而扫描无关用户/远端目录。

备份文件较新只说明写入时间，不证明项目配置完整。比较候选的项目 ID、目录、原聊天归属与数据库；用明确的来源制作快照。项目数可能是零、几个或几百个，空记录不能拿来替代有内容的记录。

## 制作项目配置快照

```text
python <skill-dir>/scripts/project_snapshot.py snapshot --state <state.json> --out <unique-snapshot.json>
python <skill-dir>/scripts/project_snapshot.py snapshot --state <state.json> --source-owner <source-partition> --out <unique-snapshot.json>
```

输出只包含已知布局的项目字段和必要的证据散列/时间，不是全局状态全量备份，不包含完整聊天正文和认证信息。它仍包含项目名称、路径和关联 ID，属于用户数据，应保存在本次 workspace 的私有工作目录或用户指定备份位置。

来源与当前目标分别留存可验证备份。目标缺少项目字段而不能导出项目快照时，保存其原始状态文件的字节级备份和散列，作为恢复前证据；不要为了通过快照检查先向 live 配置补假字段。真实写入前还要重新读取并备份最新目标，不能用计划生成时的旧副本代替。

在恢复前保存最新相关配置是恢复流程的一部分；用户单独请求备份时也可生成快照。不要因“以后可能新建项目”就擅自建立周期自动化、永久文件监听器或开机任务。每次运行重新采集；一个旧快照无法自动包含其创建之后的新项目。

## 生成并审阅计划

```text
python <skill-dir>/scripts/project_snapshot.py plan --snapshot <snapshot.json> --state <current-state.json> --target-owner <target-partition> --out <unique-plan.json>
python <skill-dir>/scripts/project_snapshot.py plan --snapshot <snapshot.json> --state <current-state.json> --target-owner <target-partition> --path-map "<old-root>=<new-root>" --out <unique-plan.json>
```

先运行 `--help` 核实具体参数。目标分区必须根据当前环境确定，不能直接使用快照来源电脑的分区。来源有多个不同账号时，先明确用户要恢复哪一组。

计划用于审阅，不直接修改 live 配置。关注动态项目/归属增量、冲突、目标基线、来源散列和有效期。目标端现有项目及其相对顺序保持不变；只有缺失项可添加，相同项不重复。矛盾的项目定义、聊天归属或排序不能静默取一方。

用户明确要求保留当前值时，记录这一取舍，将它用于新的候选快照/计划；原始证据保持不变。若要改回旧值而原指令没有明确授权，展示具体冲突让用户决定。不要直接删除 conflicts 或篡改可执行标记来跳过核对。

路径映射遵守目录边界与最长前缀。`D:/work` 不能误改 `D:/workspace`，项目名称、普通说明文字不能作为路径替换。Windows 路径和 POSIX 路径需分别解释，外来路径的不存在不等于文件被删。主机相关的 ID 映射也须核对，未解决前不执行计划。

跨机器时，原聊天 ID、服务器项目 ID 可能在目标库中不存在。路径转换本身不能验证这些关联；用本机 API/数据库核实。若用户只想导入名称与目录入口，先明确省略聊天历史的结果，不能声称历史恢复成功。

## 兼容边界

通用能力来自运行时发现和按 schema 选择处理方式，而不是假设所有版本相同。当前脚本对已有的五字段布局提供快照/合并计划；发现其他结构时，它应报告未知或不兼容，agent 再使用本机公开工具或根据实证适配。不能伪造一个“兼容”结果。

项目数据完全不存在、所有备份缺失、磁盘损坏或云端数据不可访问时，说明可验证的缺口。skill 可以继续诊断和帮助确定恢复来源，但不能保证找回没有依据的信息。
