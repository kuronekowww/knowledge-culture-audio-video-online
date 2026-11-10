"""One stationary, editable brand record from the calibrated original artwork."""
def playback_image(style, body_us, start_us=1_000_000):
    if not 0 <= start_us < body_us or style.get('mode') != 'static_png':
        raise ValueError('静态唱片需要正确的显示时间和 PNG 素材')
    return {'role':'playback','kind':'image','asset_id':'portrait__playback','start_us':start_us,
            'end_us':body_us,'box_px':style['box_px'],
            'style_reference':{'alpha':1,'x':0,'y':0,'scale_x':1,'scale_y':1}}
