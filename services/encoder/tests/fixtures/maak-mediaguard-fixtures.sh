#!/usr/bin/env bash
# Korte MediaGuard-fixtures (≈4 s, 720p, hoge bitrate zodat de bestaande
# drempels uit beslissen.STANDAARD een encodebesluit opleveren).
#
#   tests/fixtures/maak-mediaguard-fixtures.sh <doelmap>
#
# Iedere fixture dekt een MediaGuard-eis af; zie docs/AV1_DV_PLAYBACK_MATRIX.md.
# Ontbreekt een tool (dovi_tool, mkvmerge, hdr10plus_tool), dan wordt alleen die
# fixture overgeslagen en slaan de bijbehorende tests eerlijk over.
set -euo pipefail

DOEL="${1:?doelmap vereist}"
mkdir -p "$DOEL"
W="$DOEL/.werk"
mkdir -p "$W"
SECONDEN=4
FPS=24
FRAMES=$((SECONDEN * FPS))
MD="G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
X265_HDR10="hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=$MD:max-cll=1000,400:keyint=24"
X265_HLG="repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:keyint=24"
# Constante hoge bitrate: anders comprimeert testsrc2 onder de 4000-kbps-drempel.
RATE=(-b:v 9M -maxrate 9M -bufsize 4M)

heeft() { command -v "$1" >/dev/null 2>&1; }

echo "MediaGuard-fixtures in $DOEL"

# ── 1. Dual audio + NL + commentaar + audiodescriptie + forced/signs ─────────
echo "  mg-dual-audio.mkv    eng+jpn+nld, commentaar, AD, forced en signs&songs"
printf '1\n00:00:00,500 --> 00:00:02,000\nHallo\n' > "$W/full.srt"
printf '1\n00:00:01,000 --> 00:00:02,000\n[forced]\n' > "$W/forced.srt"
printf '[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name\nStyle: Default\n\n[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:01.00,0:00:02.00,Default,Sign\n' > "$W/signs.ass"
FONT=$(find /usr/share/fonts -name "*.ttf" -print -quit 2>/dev/null || true)
ATTACH=()
if [[ -n "$FONT" ]]; then
    ATTACH=(-attach "$FONT" -metadata:s:t:0 mimetype=application/x-truetype-font)
fi
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=1280x720:rate=$FPS" \
    -f lavfi -i "sine=frequency=440" -f lavfi -i "sine=frequency=550" \
    -f lavfi -i "sine=frequency=660" -f lavfi -i "sine=frequency=330" \
    -f lavfi -i "sine=frequency=220" \
    -i "$W/full.srt" -i "$W/forced.srt" -i "$W/signs.ass" -t "$SECONDEN" \
    -map 0:v -map 1:a -map 2:a -map 3:a -map 4:a -map 5:a -map 6:s -map 7:s -map 8:s \
    -c:v libx264 -preset ultrafast "${RATE[@]}" -c:a aac -b:a 96k -c:s copy -c:s:2 ass \
    -metadata:s:a:0 language=eng -metadata:s:a:0 title="English" -disposition:a:0 default \
    -metadata:s:a:1 language=jpn -metadata:s:a:1 title="Japanese" -disposition:a:1 0 \
    -metadata:s:a:2 language=dut -metadata:s:a:2 title="Nederlands" -disposition:a:2 0 \
    -metadata:s:a:3 language=eng -metadata:s:a:3 title="Director Commentary" -disposition:a:3 comment \
    -metadata:s:a:4 language=eng -metadata:s:a:4 title="Audio Description" -disposition:a:4 visual_impaired \
    -metadata:s:s:0 language=eng -metadata:s:s:0 title="Full" \
    -metadata:s:s:1 language=eng -metadata:s:s:1 title="Forced" -disposition:s:1 forced \
    -metadata:s:s:2 language=eng -metadata:s:s:2 title="Signs & Songs" \
    "${ATTACH[@]}" \
    "$DOEL/mg-dual-audio.mkv"

# ── 2. HDR10 (PQ, mastering display in frame-side-data) ─────────────────────
echo "  mg-hdr10.mkv         HDR10 PQ + mastering display + MaxCLL"
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=1280x720:rate=$FPS" -f lavfi -i "sine=frequency=440" -t "$SECONDEN" \
    -c:v libx265 -pix_fmt yuv420p10le -preset ultrafast "${RATE[@]}" -x265-params "$X265_HDR10" \
    -c:a aac -metadata:s:a:0 language=eng "$DOEL/mg-hdr10.mkv" 2>/dev/null

# ── 3. HLG-basis voor DV 8.4 ────────────────────────────────────────────────
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=1280x720:rate=$FPS" -t "$SECONDEN" \
    -c:v libx265 -pix_fmt yuv420p10le -preset ultrafast "${RATE[@]}" -x265-params "$X265_HLG" \
    -f hevc "$W/hlg.hevc" 2>/dev/null
ffmpeg -hide_banner -loglevel error -y -i "$DOEL/mg-hdr10.mkv" -map 0:v:0 -c copy \
    -bsf:v hevc_mp4toannexb -f hevc "$W/pq.hevc"

# ── 4-6. Dolby Vision 8.1 / 8.4 / 5 via dovi_tool generate + inject-rpu ────
maak_dv() {  # profiel basis.hevc uitvoer
    local profiel="$1" basis="$2" uit="$3"
    cat > "$W/dv-$profiel.json" <<JSON
{ "cm_version": "V29", "profile": "$profiel", "length": $FRAMES,
  "level6": { "max_display_mastering_luminance": 1000, "min_display_mastering_luminance": 1,
              "max_content_light_level": 1000, "max_frame_average_light_level": 400 } }
JSON
    dovi_tool generate -j "$W/dv-$profiel.json" -o "$W/rpu-$profiel.bin" >/dev/null
    dovi_tool inject-rpu -i "$basis" --rpu-in "$W/rpu-$profiel.bin" -o "$W/dv-$profiel.hevc" >/dev/null
    mkvmerge -q -o "$uit" --default-duration "0:${FPS}p" "$W/dv-$profiel.hevc" \
        --no-video "$DOEL/mg-hdr10.mkv"
}
if heeft dovi_tool && heeft mkvmerge; then
    echo "  mg-dv81.mkv          DV 8.1 (PQ-basis met HDR10-mastering)"
    maak_dv 8.1 "$W/pq.hevc" "$DOEL/mg-dv81.mkv"
    echo "  mg-dv84.mkv          DV 8.4 (HLG-basis)"
    maak_dv 8.4 "$W/hlg.hevc" "$DOEL/mg-dv84.mkv"
    echo "  mg-dv5.mkv           DV 5 (moet altijd 'niet ondersteund' blijven)"
    maak_dv 5 "$W/pq.hevc" "$DOEL/mg-dv5.mkv"
else
    echo "  mg-dv*.mkv           OVERGESLAGEN (dovi_tool of mkvmerge ontbreekt)"
fi

# ── 7. HDR10+ (ST 2094-40) via hdr10plus_tool inject ────────────────────────
if heeft hdr10plus_tool && heeft mkvmerge; then
    echo "  mg-hdr10plus.mkv     HDR10 + HDR10+ dynamische metadata"
    {
        printf '{"JSONInfo":{"HDR10plusProfile":"B","Version":"1.0"},"SceneInfo":['
        for ((i = 0; i < FRAMES; i++)); do
            [[ $i -gt 0 ]] && printf ','
            printf '{"BezierCurveData":{"Anchors":[102,205,307,410,512,614,717,819,922],"KneePointX":0,"KneePointY":0},"LuminanceParameters":{"AverageRGB":100,"LuminanceDistributions":{"DistributionIndex":[1,5,10,25,50,75,90,95,99],"DistributionValues":[10,20,30,40,50,60,70,80,90]},"MaxScl":[1000,1000,1000]},"NumberOfWindows":1,"TargetedSystemDisplayMaximumLuminance":400,"SceneFrameIndex":%d,"SceneId":0,"SequenceFrameIndex":%d}' "$i" "$i"
        done
        printf '],"SceneInfoSummary":{"SceneFirstFrameIndex":[0],"SceneFrameNumbers":[%d]},"ToolInfo":{"Tool":"mediaguard-fixture","Version":"1"}}' "$FRAMES"
    } > "$W/hdr10plus.json"
    hdr10plus_tool inject -i "$W/pq.hevc" -j "$W/hdr10plus.json" -o "$W/hdr10plus.hevc" >/dev/null
    mkvmerge -q -o "$DOEL/mg-hdr10plus.mkv" --default-duration "0:${FPS}p" "$W/hdr10plus.hevc" \
        --no-video "$DOEL/mg-hdr10.mkv"
else
    echo "  mg-hdr10plus.mkv     OVERGESLAGEN (hdr10plus_tool of mkvmerge ontbreekt)"
fi

rm -rf "$W"
echo "Klaar."
