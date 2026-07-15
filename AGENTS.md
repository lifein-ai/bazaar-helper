# AGENTS.md

## 修改原则

- 修改前先阅读相关代码，确认真实调用链和实际生效的实现。
- 只做与当前任务相关的最小修改，不整体重写、格式化或重构无关文件。
- 不创建重复的 Prompt、函数、常量或配置。

## 编码安全

- 所有源码和文本文件统一使用 UTF-8。
- 禁止使用 PowerShell `Set-Content`、`Out-File`、`echo` 或重定向覆盖源码。
- 修改已有文件优先使用局部补丁。
- 禁止将正常中文批量转换为 `\uXXXX`。

发现乱码、`�`、异常大量 `\uXXXX` 或大范围无关改动时：

1. 立即停止写入；
2. 不继续尝试 GBK、UTF-8 相互转换；
3. 先检查 Git 状态和 Diff；
4. 优先从 Git 或备份恢复。

## Git 安全

- 修改前检查 `git status`。
- 未经允许，不执行 `git reset --hard`、`git clean -fd`、`git restore .` 等可能丢失修改的命令。

## 修改后检查

完成后检查：

```bash
git diff
python -m compileall src
```

确认：

- 没有无关修改；
- 没有乱码或 `�`；
- 没有异常大量 `\uXXXX`；
- 没有重复实现；
- 语法检查通过。
