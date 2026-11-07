# 专辑画风确认与持久化

用户制作时会提供专辑封面，优先视觉读取并提炼其色彩、材质、氛围；封面明确指定的方向就是用户风格选择，记录实际指令，不强制再三选一。既没有封面参考也没有明确文字方向时才根据实际内容提出三种适配画风。后续机制和配置如下，声音封面/共用底图详见[专辑视觉资产](album-visuals.md)。

开始先查看`<shared_root>/albums/<album_id>/visual_style.json`。已有真实确认档案时复用，不因新批次、新声音或更换执行模型再次询问。共享目录必须是持久化且同用户可访问的同一路径；多实例使用共同文件系统。不同专辑不按同名推断为同一份画风。

首次结合专辑实际内容提出三种不同表现方式，各给名称、适配原因和完整风格描述（表现方式＋色彩＋光线＋材质）。不固定三套通用答案：例如艺术细读适合纸张肌理插画，科学理解可能适合清晰的编辑插画或物件示意，历史文化可能适合克制的历史情境插画。历史演绎不能伪称史料。用户已有明确指定风格时可只提供这一种，不强制再选一次。

品牌默认开启，只有用户明确关闭才设false；未提供封面不等于关闭品牌。记录用户实际回复，不能把本参考示例当作用户选择；没有选择就停在画风待办，不生图。无需收费风格测试图。示例：

```json
{"creative_options":{
  "branding_enabled":true,
  "album_tone":"根据本专辑实际内容填写",
  "style_candidates":[
    {"id":"A","name":"编辑插画","reason":"适合解释具体认知问题","prompt":"二维编辑插画，统一低饱和蓝灰和暖橙点色，柔和散射光，细腻纸张质感，物件轮廓清楚"},
    {"id":"B","name":"物件写实","reason":"适合呈现真实生活情境","prompt":"写实物件静帧，统一暖灰与木色，柔和侧光，真实材质，克制构图"},
    {"id":"C","name":"文化手绘","reason":"适合细读文化意象","prompt":"手绘二维插画，统一米白与靛蓝，柔和漫射光，细纸纹理，线条清晰"}
  ],
  "style_id":"A",
  "style_confirmation":{"confirmed_by":"user","evidence":"填写本会话中用户实际选择的原话及可追溯位置"}
}}
```

flow select先验证整批选段，再调用creative_options.resolve_album：首次选择保存visual_style.json，带album_id、version、原选择和selection_hash。每个job固定creative_options及album_style_version。后续selection可省略整个creative_options，由共享档案恢复；仅改变品牌时可传`{"creative_options":{"branding_enabled":true}}`，沿用画风。

明确更换画风：提供新候选/选择、实际用户确认及`creative_options.replaces_style_version`（当前档案版本）。旧档案写visual_style_history/<version>.json，新任务使用新版本，旧job仍固定旧确认版本。需要让已有job换风格时在其配置明确更新同一选择再resume；不暗中重生所有历史图片。只切品牌不重跑ASR或生图。

bind_prompts把所选style.prompt编译为每条图片提示词固定前缀，并保存style_selection_hash；重复恢复不叠加前缀。角色、器物、时代与连续场景在visual_bible内固定，实际提示词需展开完整信息，接口不会自动读取bible，也没有未经接入的seed/参考图功能。统一画风意味着相似视觉语言，不保证每张图像素或人物面貌一致。
