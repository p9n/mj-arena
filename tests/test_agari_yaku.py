"""Unit tests for Agari decomposition, Fu, Yaku, Yakuman, and Pao scoring."""

from __future__ import annotations

import unittest

from server.agari import (
    LOCAL_YAKU_REGISTRY,
    WinContext,
    compute_score_deltas,
    evaluate_agari,
    register_local_yaku,
)
from server.config import RuleConfig
from server.hand import Meld
from server.tile import parse_mpsz


class AgariYakuTest(unittest.TestCase):

  def test_pinfu_tsumo_20fu(self) -> None:
    hand13 = parse_mpsz("123m456p23s789s99p")
    ctx = WinContext(
        is_tsumo=True,
        winning_token="1s",
        seat_wind_kind34=28,  # S
        round_wind_kind34=27,  # E
        is_dealer=False,
    )
    res = evaluate_agari(hand13, [], ctx)
    self.assertTrue(res.is_valid)
    self.assertEqual(res.fu, 20)
    self.assertEqual(res.han, 2)
    yaku_names = [n for n, _ in res.yaku]
    self.assertIn("平和", yaku_names)
    self.assertIn("門前清自摸和", yaku_names)
    self.assertEqual(res.total_points, 1500)  # 400/700 -> 1500

  def test_dora_only_is_invalid(self) -> None:
    """※懸賞役は縛りの飜数には数えない."""
    hand13 = parse_mpsz("111m406p23s789s11z")
    ctx = WinContext(
        is_tsumo=False,
        winning_token="1s",
        seat_wind_kind34=28,  # S
        round_wind_kind34=29,  # W (so 11z is not yakuhai, and 111m prevents Pinfu)
        is_dealer=False,
        dora_indicators=["9m"],  # 1m is dora, plus 0p aka dora
    )
    res = evaluate_agari(hand13, [], ctx)
    self.assertFalse(res.is_valid)

  def test_renpuuhai_4fu_and_rinshan_2fu(self) -> None:
    """連風牌は4符, 嶺上ツモも2符加算."""
    # Closed hand with double-east pair (11z) + kanchan wait on 2p + 2 melds + 1 ankan
    hand13 = parse_mpsz("13p234s11z")
    melds = [
        Meld("pon", ["2m", "2m", "2m"], "2m", 1),
        Meld("ankan", ["9s", "9s", "9s", "9s"]),
    ]
    ctx = WinContext(
        is_tsumo=True,
        winning_token="2p",
        seat_wind_kind34=27,  # E
        round_wind_kind34=27,  # E (double wind pair = 4 fu)
        is_dealer=True,
        is_rinshan=True,
    )
    res = evaluate_agari(hand13, melds, ctx)
    self.assertTrue(res.is_valid)
    # Fu: base 20 + tsumo(rinshan) 2 + renpuuhai pair 4 + kanchan 2 + open 2m koutsu 2 + closed 9s kantsu 32 = 62 -> 70 fu
    self.assertEqual(res.fu, 70)
    self.assertEqual(res.han, 1)
    self.assertEqual(res.yaku, [("嶺上開花", 1)])

  def test_ron_koutsu_is_minkou_vs_shuntsu_ankou(self) -> None:
    """※ロン和了で完成した刻子は明刻子とし順子は暗順子とする."""
    # Hand: 111m 222p 33s 44s, winning on 3s by Ron -> 333s is open koutsu, so only 2 ankou (no 三暗刻)
    hand13 = parse_mpsz("111m222p456p33s44s")
    ctx = WinContext(
        is_tsumo=False,
        winning_token="3s",
        seat_wind_kind34=28,
        round_wind_kind34=27,
        is_dealer=False,
        is_riichi=True,
    )
    res = evaluate_agari(hand13, [], ctx)
    self.assertTrue(res.is_valid)
    yaku_names = [n for n, _ in res.yaku]
    self.assertNotIn("三暗刻", yaku_names)

    # But if 333s is already in hand and 3s completes 234s sequence alongside 333s:
    # 111m 222p 23334s 77z, winning on 3s by Ron -> 3s can complete 234s (暗順子), keeping 333s as 暗刻子 -> 三暗刻!
    hand13_sanankou = parse_mpsz("111m222p23334s77z")
    res2 = evaluate_agari(hand13_sanankou, [], ctx)
    self.assertTrue(res2.is_valid)
    yaku_names2 = [n for n, _ in res2.yaku]
    self.assertIn("三暗刻", yaku_names2)

  def test_chiitoitsu_25fu_and_tsuuiisou_chiitoitsu(self) -> None:
    """※七対子は25符2飜、ツモ符なし; ※七対子の字一色あり."""
    hand13 = parse_mpsz("1199m2288p3377s5z")
    ctx = WinContext(
        is_tsumo=True,
        winning_token="5z",
        seat_wind_kind34=27,
        round_wind_kind34=27,
        is_dealer=True,
    )
    res = evaluate_agari(hand13, [], ctx)
    self.assertTrue(res.is_valid)
    self.assertEqual(res.fu, 25)
    self.assertEqual(res.han, 3)  # 七対子(2) + 門前清自摸和(1)

    # Chiitoitsu Tsuuiisou (7 honor pairs)
    hand13_honors = parse_mpsz("1122334455667z")
    ctx_h = WinContext(
        is_tsumo=False,
        winning_token="7z",
        seat_wind_kind34=27,
        round_wind_kind34=27,
        is_dealer=False,
    )
    res_h = evaluate_agari(hand13_honors, [], ctx_h)
    self.assertTrue(res_h.is_valid)
    self.assertEqual(res_h.yakuman, ["字一色"])
    self.assertEqual(res_h.total_points, 32000)

  def test_composite_yakuman_and_pao(self) -> None:
    """大三元/大四喜のパオあり。複合役満を含む得点を、ツモ＝全額・ロン＝折半で支払う。積み棒は包。"""
    # Tsuuiisou + Daisangen (double yakuman for non-dealer = 64000)
    # 3rd dragon meld called from seat 2 (Pao)
    hand13 = parse_mpsz("1112z")
    melds = [
        Meld("pon", ["5z", "5z", "5z"], "5z", 1),
        Meld("pon", ["6z", "6z", "6z"], "6z", 1),
        Meld("pon", ["7z", "7z", "7z"], "7z", 2),
    ]
    ctx_tsumo = WinContext(
        is_tsumo=True,
        winning_token="2z",
        seat_wind_kind34=28,
        round_wind_kind34=27,
        is_dealer=False,
        pao_seats={"大三元": 2},
    )
    res = evaluate_agari(hand13, melds, ctx_tsumo)
    self.assertTrue(res.is_valid)
    self.assertEqual(set(res.yakuman), {"字一色", "大三元"})
    self.assertEqual(res.total_points, 64000)

    # Tsumo with Pao (seat 2 pays full 64000 + 300*honba)
    deltas_tsumo = compute_score_deltas(
        winner_seat=1,
        loser_seat=None,
        dealer_seat=0,
        agari=res,
        honba=2,
        kyoutaku=1,
        pao_seats={"大三元": 2},
    )
    self.assertEqual(deltas_tsumo, [0, 64000 + 600 + 1000, -(64000 + 600), 0])

    # Ron on seat 3 with Pao on seat 2 (half 32000 each, seat 2 pays full 600 honba)
    deltas_ron = compute_score_deltas(
        winner_seat=1,
        loser_seat=3,
        dealer_seat=0,
        agari=res,
        honba=2,
        kyoutaku=1,
        pao_seats={"大三元": 2},
    )
    self.assertEqual(
        deltas_ron, [0, 64000 + 600 + 1000, -(32000 + 600), -32000]
    )

  def test_local_yaku_registry(self) -> None:
    """Verify extensible local yaku registration via config."""
    register_local_yaku(
        "test_custom_yaku",
        lambda closed14, melds, decomp, ctx: [("テスト地方役", 2, False)],
    )
    try:
      hand13 = parse_mpsz("123m456p23s789s11z")
      ctx = WinContext(
          is_tsumo=False,
          winning_token="1s",
          seat_wind_kind34=28,
          round_wind_kind34=29,
          is_dealer=False,
          local_yaku_enabled={"test_custom_yaku": True},
          rules=RuleConfig(),
      )
      res = evaluate_agari(hand13, [], ctx)
      self.assertTrue(res.is_valid)
      self.assertIn(("テスト地方役", 2), res.yaku)
    finally:
      LOCAL_YAKU_REGISTRY.pop("test_custom_yaku", None)


if __name__ == "__main__":
  unittest.main()
