# Windows 可执行文件的版本/作者信息（PyInstaller 打包时读取）。
# 注意：这里只能有一条 VSVersionInfo(...) 表达式，不能加文档字符串/其它语句，
# 因为 PyInstaller 是直接 eval 整个文件内容的。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(3, 0, 0, 0),
    prodvers=(3, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(2026, 9, 27, 0, 0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'080404B0',
          [
            StringStruct(u'CompanyName', u'一键三连'),
            StringStruct(u'FileDescription', u'DDO 龙与地下城OL 聊天翻译助手'),
            StringStruct(u'FileVersion', u'3.0.0.0'),
            StringStruct(u'InternalName', u'DDOTranslator'),
            StringStruct(u'LegalCopyright', u'Copyright (C) 2026 一键三连'),
            StringStruct(u'OriginalFilename', u'DDO翻译助手_v3.0.0.exe'),
            StringStruct(u'ProductName', u'DDO翻译助手'),
            StringStruct(u'ProductVersion', u'3.0.0.0'),
            StringStruct(u'Comments', u'作者：一键三连　主页：https://gitee.com/git55236/ddo-chat-translator'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
