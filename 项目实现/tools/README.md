# 仓库内置工具

`convert_carsim_vsb.py` 是项目运行需要的 VS/VSB 转换器。它只使用 Python 标准库，随源码一起发布，
避免依赖开发者电脑上的兄弟目录。

BLF 解码依赖通过 `部署包/requirements.txt` 安装的 `python-can` 和 `cantools` 提供，仓库不再打包
开发者本机的 Python site-packages。
