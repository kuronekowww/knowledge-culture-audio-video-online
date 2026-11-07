# 文字接口

所有字号、字距、颜色和坐标由layout-profile.json经layout_profile编译。具体字体、文案宽度、两行专辑名和原声字幕换行规则见[统一模板](branding-layout.md)。禁止用API默认字体、历史字数上限或旧字号作为布局来源。

caption_api仅向add_captions发送服务实际支持的字段，所有文字从配置的text_start_us开始，原声时码不平移。正文title_line1/title_line2单行，各自执行真实字体测宽。专辑名可按编译结果换行，字幕换行保持原文字词。没有CTA/notice或来源文字轨道。

HTTP服务只识别字体目录名，未知字体可能静默回退；因此保存下载后必须执行jianying_profile.finalize，写入指定本地字体路径、字距、描边及精确坐标，再运行draft_verify。未绑定字体或哈希不符不得标记草稿通过。OpenFilm直接嵌入固定展示字体，字幕绑定本机苹方Medium。两端均不通过更换字库或减字号规避超宽。
