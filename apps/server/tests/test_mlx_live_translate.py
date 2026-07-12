# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_START ===
from __future__ import annotations

from apps.server.ai.mlx_live_translate import guard_mlx_ko


class TestGuardMlxKo:
    def test_clean_translation_passes(self):
        assert guard_mlx_ko(
            "And I put all of my projects in my documents folder.",
            "그리고 저는 모든 프로젝트를 문서 폴더에 저장합니다.",
        ) is None

    def test_partial_english_terms_allowed(self):
        # 기술 자막에서 흔한 부분 영어 잔존은 허용
        assert guard_mlx_ko(
            "Please turn this into a landing page.",
            "이걸 landing page로 만들어 주세요.",
        ) is None

    def test_cjk_hanzi_rejected(self):
        assert guard_mlx_ko("So this is codex.", "이것이 코다克斯입니다.") == "foreign_script"

    def test_kana_rejected(self):
        assert guard_mlx_ko("Let's do it.", "해보ましょう.") == "foreign_script"

    def test_cyrillic_rejected(self):
        assert guard_mlx_ko("Open codex.", "코드КС를 여세요.") == "foreign_script"

    def test_replacement_char_rejected(self):
        assert guard_mlx_ko("Open it.", "여세요�.") == "foreign_script"

    def test_invented_number_rejected(self):
        # 벤치 실측: EN에 숫자가 없는데 "53만 달러" 환각
        assert guard_mlx_ko(
            "I will create a new project.", "53만 달러로 새 프로젝트를 만들 것입니다."
        ) == "invented_number"

    def test_number_present_in_en_passes(self):
        assert guard_mlx_ko("On base 44.", "베이스 44에서요.") is None

    def test_en_digit_missing_in_ko_allowed(self):
        # KO가 숫자를 한글로 풀어쓴 경우 허용 (EN→KO 방향 누락은 통과)
        assert guard_mlx_ko("It takes 2 minutes.", "이 분 정도 걸립니다.") is None

    def test_empty_rejected(self):
        assert guard_mlx_ko("Hello there.", "") == "empty"
        assert guard_mlx_ko("Hello there.", "   ") == "empty"

    def test_length_explosion_rejected(self):
        assert guard_mlx_ko("Hi.", "이 문장은 원문보다 지나치게 길어진 설명 폭주 사례입니다." * 3) == "length_ratio"

    def test_length_collapse_rejected(self):
        long_en = "And I can say, please, turn this into a landing page, a good learning resource for my viewers."
        assert guard_mlx_ko(long_en, "네.") == "length_ratio"

    def test_english_leak_rejected(self):
        assert guard_mlx_ko(
            "I can mention any file created within this folder.",
            "I can mention any file 폴더.",
        ) == "english_leak"

    def test_repetition_rejected(self):
        # 벤치 실측: "분류하고 분류하여" 류 반복 붕괴
        chunk = "분류하고 정리하여 저장하는 "
        assert guard_mlx_ko(
            "Sort and organize the files in the folder now.", chunk * 4
        ) == "repetition"
# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_END ===
