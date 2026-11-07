# 内置组装入口

素材就绪后默认执行 `python scripts/assemble_draft.py --job-dir /job`。本地/线上模式、服务配置、回执恢复及自动导入详见[draft-assembly.md](draft-assembly.md)。以下保留逐项请求契约供兼容环境核对；使用内置入口后不要再手工重放。

# 拼装顺序与保存后真实草稿核验

读取capcut_handoff.json，只执行唯一portrait时间线中的api_sequence.steps。每一步记录step_id、请求哈希、响应和最新draft_url；同一草稿串行写。图片/字幕已按数量与字节分组，列表字段是JSON字符串，不再编码一次。接口实际不兼容时报告具体差异，不擅自删镜头/字幕或改时长。

```bash
python scripts/assembly_api.py --job-dir /job --orientation portrait --step-index 0 --output /job/request.json
# 后续请求另传 --draft-url 当前草稿最新成功响应的完整URL
```

请求顺序和坐标由assembly_api.compile_timeline生成：创建画布→独立底图→封面/原始正文图片→原声音轨→品牌组件/完整片尾→文字→保存。图片不预合成，保存后完成字体与精确位置绑定；详见branding-layout.md。

长作业从真实成功的step继续，结果未知先查询现有草稿，不能从头重复添加。下游真实服务如果限制时长/片段总数，明确报告并按自然价值单元调整方案，不自动加速压短或只提交前一部分。

save_draft成功记录“草稿已保存，待核验”。取回该保存版本的原始JSON及所有媒体，再执行：

```bash
python scripts/draft_verify.py --job-dir /job --orientation portrait --draft /downloaded/portrait-draft
```

校验画布和总长、完整片段数、真实媒体字节哈希、每段时码、原声音量/源区间、正文标题、字幕、专辑名与来源图片的哈希/位置/缩放/层级，以及不存在旧来源文字轨道、CTA/AI提示或首秒文字、完整画布图和品牌片尾音画轨。仅passed交付为草稿核验通过。

报告draft_verification.portrait.json绑定交接/JSON/媒体/校验器版本。结构通过不等于线上渲染视觉或平台审核通过。失败只改对应步骤，保留已成功上游素材。剪映API路径要求MP4时须真实下游导出；用户选择OpenFilm则走[独立输出后端](openfilm-output.md)的本机原生渲染与核验，不声称OpenFilm验证等同剪映端到端验证。

播放贴纸、专辑封面、来源图片的大小与位置全部从layout-profile.json派生，不在此维护第二套参数。原生贴纸只添加一个片段；OpenFilm使用配置锁定的原动画。

不再编译/发送末尾钩子字幕或“AI示意画面”；音轨从0开始，不全局后移字幕。贴纸显示从1秒起，单次请求先于专辑小封面。旧请求若首秒有文字、角色cta/notice、标题换行或字号16，将被装配前校验拒绝。
