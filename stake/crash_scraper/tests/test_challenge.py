from stake_crash_scraper.challenge import contains_challenge_marker


def test_detects_common_manual_challenges():
    assert contains_challenge_marker(["Verify you are human"])
    assert contains_challenge_marker(["https://example.com/cdn-cgi/challenge-platform/foo"])
    assert contains_challenge_marker(["Complete the security check"])


def test_normal_page_text_is_not_a_challenge():
    assert not contains_challenge_marker([
        "Stake Crash",
        "123 Playing",
        "Live bets and results",
    ])
