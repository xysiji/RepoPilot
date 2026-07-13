# RepoPilot 项目规则

1. 每次只开发一个阶段，当前阶段的范围必须明确且有限。
2. 开发前先阅读与当前阶段有关的设计文档。
3. `reference_materials/` 中的资料只按需读取，禁止在每次开发时扫描全部文件。
4. 每个阶段必须在 `docs/learning/` 中生成对应的学习笔记。
5. 每个阶段必须在 `docs/interview/` 中生成面试题，并在 `docs/interview/answers/` 中生成对应答案。
6. 每个阶段完成后必须运行测试并检查 Git Diff。
7. 当前阶段完成后不得自动开始下一阶段，必须等待用户明确指示。
8. 不得把 `reference_materials/` 中的内容复制到公开的 `README.md` 或其他公开材料。
9. 不得为了尚未确认的未来需求过度设计或提前实现。
10. 安全边界必须通过代码进行验证，不能只依赖 Prompt 约束。

## Windows Python environment

This project requires Python 3.12.

The canonical Windows project interpreter is:

`.\.venv\Scripts\python.exe`

Codex desktop tasks do not assume that an external PowerShell virtual environment has been inherited.

Do not use bare `python`, `pip`, or `pytest` commands for project work. Use:

- `.\.venv\Scripts\python.exe`
- `.\.venv\Scripts\python.exe -m pip`
- `.\.venv\Scripts\python.exe -m pytest`

The system Python 3.14 installation must not be used for this project.
