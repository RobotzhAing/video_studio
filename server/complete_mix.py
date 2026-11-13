"""Policy and timeline assembly for fully voiced AI batch exports."""
import re
import unicodedata
import wave
from fractions import Fraction


class MixError(ValueError):
    pass


MAX_SECONDS = 300
LOCALE = re.compile(r'[a-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?\Z')
OVERRIDE = re.compile(
    r'^\s*(配音语言|旁白语言|字幕语言|配乐风格|背景音乐风格)\s*[:：]\s*(\S[^\r\n]*)$', re.M)



_RATIO_TOKEN = re.compile(r'(?<![\d.])([0-9]{1,5}(?:\.[0-9]{1,4})?)\s*([:/x×])\s*'
                          r'([0-9]{1,5}(?:\.[0-9]{1,4})?)(?![\d.:])', re.I)
_ASPECT_WORDS = {
    '竖屏': '9:16', '竖版': '9:16', '直式': '9:16', 'portrait': '9:16', 'vertical': '9:16',
    '横屏': '16:9', '横版': '16:9', 'landscape': '16:9', 'horizontal': '16:9',
    '方形': '1:1', '正方形': '1:1', 'square': '1:1',
}
_ASPECT_WORD = re.compile(r'竖屏|竖版|直式|横屏|横版|正方形|方形|\b(?:portrait|vertical|landscape|horizontal|square)\b', re.I)
_OUTPUT_LABEL = re.compile(
    r'^(?:[-*]\s*)?(?:输出|导出|成片|目标|最终(?:成片)?)\s*(?:画面)?(?:宽高比|画幅|比例|分辨率|尺寸)?\s*:'
    r'|^(?:[-*]\s*)?(?:output|export|target|final(?:\s+video)?)'
    r'\s*(?:aspect\s+ratio|aspect|ratio|size|resolution|format)?\s*:', re.I)
_NEUTRAL_LABEL = re.compile(r'^(?:[-*]\s*)?(?:画面比例|宽高比|画幅|比例|aspect\s+ratio)\s*:', re.I)
_OUTPUT_DIRECTIVE = re.compile(
    r'导出|输出|制作|制成|剪成|剪辑为|生成|做成|采用|设为|请按|(?:按|使用)(?=.*(?:比例|画幅|宽高比))|成片(?:须|要|应|改为|用|为)'
    r'|\b(?:export|render|output|deliver|produce)\b|\bfinal\s+video\s+(?:should|must|will)\b', re.I)
_REFERENCE_DESCRIPTION = re.compile(
    r'^(?:[\d一二三四五六七八九十]+[.)、]\s*)?(?:参考(?:视频|片)?|原片|原视频|素材|源视频)'
    r'\s*(?:的|是|为|采用|使用|画面|画幅|比例|宽高比|分辨率|:|$)'
    r'|^(?:the\s+)?(?:reference|source|input|original)(?:\s+(?:video|clip|footage))?\s+(?:is|has|uses|aspect|ratio|resolution)', re.I)
_SECTION = re.compile(r'^(?:#{1,6}\s+|[一二三四五六七八九十]+[、.]\s*|\d+[.)、]\s*)(.+)$')
_ANALYSIS_SECTION = re.compile(r'参考|原片|素材分析|结构分析|拆解|\breference\b|\banalysis\b', re.I)
_NEGATION = re.compile(r'(?:不要|不用|不是|避免|禁止|切勿|rather\s+than|instead\s+of|\bnot|no)\s*(?:用|选|采用|导出|输出|成片|为|an?\s+)?\s*$', re.I)


def _output_ratio(prompt):
    """Extract a requested ratio while ignoring descriptions of reference/source footage."""
    if not isinstance(prompt, str):
        return None
    chosen = None
    rank = 0
    analyzing_reference = False
    for raw_line in unicodedata.normalize('NFKC', prompt).splitlines():
        line = raw_line.strip().replace('**', '').replace('`', '')
        heading = _SECTION.match(line)
        if heading:
            title = heading.group(1)
            # Treat numbered text with an aspect as prose, not a heading.
            if not _RATIO_TOKEN.search(title) and not _ASPECT_WORD.search(title):
                analyzing_reference = bool(_ANALYSIS_SECTION.search(title))
                continue
            line = title
        for clause in re.split(r'[。；;，,!！?？]|但是?|而(?=成片|最终)|\bbut\b', line, flags=re.I):
            clause = clause.strip()
            if not clause or _REFERENCE_DESCRIPTION.search(clause):
                continue
            directive = _OUTPUT_DIRECTIVE.search(clause)
            if _OUTPUT_LABEL.search(clause) or directive:
                priority = 2
            elif not analyzing_reference and _NEUTRAL_LABEL.search(clause):
                priority = 1
            elif not analyzing_reference and (_RATIO_TOKEN.fullmatch(clause) or _ASPECT_WORD.fullmatch(clause)):
                priority = 1
            else:
                continue
            if priority < rank:
                continue
            if directive and _NEGATION.search(clause[:directive.start()]):
                continue
            candidates = []
            for match in _RATIO_TOKEN.finditer(clause):
                if _NEGATION.search(clause[:match.start()]):
                    continue
                lhs, op, rhs = match.groups()
                if not Fraction(rhs) or not Fraction(lhs):
                    continue
                # Ignore timestamp-like tokens (00:12, 01:02).
                if op == ':' and (lhs.startswith('0') or rhs.startswith('0')):
                    continue
                ratio = Fraction(lhs) / Fraction(rhs)
                if Fraction(1080, 7680) <= ratio <= Fraction(7680, 1080):
                    candidates.append(ratio)
            if candidates:
                ratio = candidates[0]
            else:
                word = next((m for m in _ASPECT_WORD.finditer(clause)
                             if not _NEGATION.search(clause[:m.start()])), None)
                if not word:
                    continue
                ratio = Fraction(_ASPECT_WORDS[word[0].lower()].replace(':', '/'))
            # Constrain the aspect to the canvas renderer's dimension limit.
            if Fraction(1080, 7680) <= ratio <= Fraction(7680, 1080):
                chosen, rank = ratio, priority
    return chosen


def _media_ratio(media):
    if not isinstance(media, dict):
        return None
    w = media.get('display_width') or media.get('width')
    h = media.get('display_height') or media.get('height')
    if any(type(x) is not int or x <= 0 or x > 32768 for x in (w, h)):
        return None
    ratio = Fraction(w, h)
    return ratio if Fraction(1080, 7680) <= ratio <= Fraction(7680, 1080) else None


def output_canvas(prompt, reference=None, source=None):
    """Honor explicit output instructions, then displayed reference and source ratios."""
    ratio, origin = _output_ratio(prompt), 'prompt'
    if ratio is None:
        ratio, origin = _media_ratio(reference), 'reference'
    if ratio is None:
        ratio, origin = _media_ratio(source), 'source'
    if ratio is None:
        ratio, origin = Fraction(16, 9), 'default'
    if ratio >= 1:
        width, height = 2 * round(540 * ratio), 1080
    else:
        width, height = 1080, 2 * round(540 / ratio)
    return {'width': width, 'height': height, 'aspectRatio': f'{ratio.numerator}:{ratio.denominator}',
            'canvasSource': origin}

def contains_han(text):
    # Normalize compatibility forms and cover supplementary Han extensions,
    # radicals and ideographic numerals without rejecting kana or Hangul.
    for char in unicodedata.normalize('NFKC', text):
        cp = ord(char)
        if (0x2e80 <= cp <= 0x2fdf or cp in (0x3005, 0x3007, 0x3038, 0x3039, 0x303a, 0x303b)
                or 0x31c0 <= cp <= 0x31ef or 0x3400 <= cp <= 0x4dbf
                or 0x4e00 <= cp <= 0x9fff or 0xf900 <= cp <= 0xfaff
                or 0x16fe2 <= cp <= 0x16fe3 or 0x20000 <= cp <= 0x2ffff
                or 0x30000 <= cp <= 0x3347f):
            return True
    return False


def prompt_overrides(prompt):
    fields = {'配音语言': 'speech_locale', '旁白语言': 'speech_locale',
              '字幕语言': 'subtitle_locale', '配乐风格': 'music_style', '背景音乐风格': 'music_style'}
    values = {}
    for label, value in OVERRIDE.findall(prompt):
        key = fields[label]
        value = value.strip()
        if key == 'music_style' or LOCALE.fullmatch(value):
            values[key] = value
    return values


def check_script(raw, prompt, duration, resolve_music_style):
    if not isinstance(raw, dict):
        raise MixError('剪辑模型没有返回完整的旁白、配乐及字幕方案。')
    overrides = prompt_overrides(prompt)
    speech_locale = overrides.get('speech_locale', raw.get('speech_locale'))
    subtitle_locale = overrides.get('subtitle_locale', raw.get('subtitle_locale') or speech_locale)
    for locale in (speech_locale, subtitle_locale):
        if not isinstance(locale, str) or not LOCALE.fullmatch(locale):
            raise MixError('剪辑模型没有返回有效语言代码，例如 en-US、ja-JP。')
    music_style = resolve_music_style(overrides.get('music_style', raw.get('music_style')))
    voice_style = raw.get('voice_style') or ''
    if not isinstance(voice_style, str) or len(voice_style) > 300:
        raise MixError('剪辑模型返回的声线描述无效。')
    cues = raw.get('cues')
    if not isinstance(cues, list) or not 1 <= len(cues) <= min(16, max(1, int(duration))):
        raise MixError('剪辑模型没有返回有效的旁白和字幕分句，请缩短旁白要求后重试。')
    for cue in cues:
        if not isinstance(cue, dict):
            raise MixError('旁白或字幕分句格式无效。')
        for key in ('speech', 'subtitle'):
            value = cue.get(key)
            if (not isinstance(value, str) or not value.strip() or len(value) > (300 if key == 'speech' else 160)
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                    or not any(char.isalpha() for char in value)):
                raise MixError('旁白或字幕分句为空或过长，请缩短内容后重试。')
        if contains_han(cue['subtitle']):
            raise MixError('模型返回的字幕含汉字，已停止导出。日语请用假名，中文请用拼音。')
    return speech_locale, subtitle_locale, music_style, voice_style, cues


def pack_narration(paths, output, total_frames, transform):
    """Fit whole synthesized phrases into picture, then time captions to audio.

    All inputs and output are 16-bit mono 48 kHz PCM WAV. Whole phrases
    are accelerated uniformly (up to 1.35x); speech is never truncated.
    """
    rate = 48000

    def read(path):
        try:
            with wave.open(str(path), 'rb') as track:
                size = track.getnframes()
                if ((track.getnchannels(), track.getsampwidth(), track.getframerate()) != (1, 2, rate)
                        or not rate // 20 <= size <= rate * MAX_SECONDS):
                    raise MixError('旁白服务返回了不受支持的音频格式或时长。')
                samples = track.readframes(size)
                if len(samples) != size * 2:
                    raise MixError('旁白音频不完整。')
                return size, samples
        except (OSError, EOFError, wave.Error) as exc:
            raise MixError('无法读取生成的旁白。') from exc

    lengths = [read(path)[0] for path in paths]
    gap = 5760  # 120 ms between phrases
    available = total_frames - gap * (len(paths) - 1)
    ratio = sum(lengths) / max(1, available)
    if ratio > 1.33:
        raise MixError('生成的旁白太长，无法在不截断语音的情况下放入成片。请缩短旁白或延长成片。')
    if ratio > 1:
        paths = [transform(path, min(1.35, ratio * 1.015)) for path in paths]
    chunks = [read(path) for path in paths]
    used = sum(size for size, _ in chunks) + gap * (len(chunks) - 1)
    if used > total_frames:
        raise MixError('旁白超出了成片时长，请缩短旁白后重试。')
    cursor = 0
    timestamps = []
    with wave.open(str(output), 'wb') as track:
        track.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        for index, (size, samples) in enumerate(chunks):
            if index:
                track.writeframesraw(bytes(gap * 2))
                cursor += gap
            track.writeframesraw(samples)
            timestamps.append((cursor / rate, (cursor + size) / rate))
            cursor += size
    return timestamps, cursor / rate
