#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../tests/fixtures"
say -v Zuzana -o /tmp/v2t_cs.aiff "Dobrý den, toto je zkušební nahrávka pro přepis řeči. Dnes probereme rozpočet a termíny dodávek."
afconvert -f m4af -d aac /tmp/v2t_cs.aiff cs_sample.m4a
say -v Zuzana -o /tmp/v2t_sil.aiff "[[slnc 10000]]"
afconvert -f m4af -d aac /tmp/v2t_sil.aiff silence.m4a
printf 'this is not audio\n' > not_audio.m4a
