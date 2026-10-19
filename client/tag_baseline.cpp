// Overtime client: Maximizes count of final_pt == 0 (Gosha-Rokuiri +-0)
// Compile: g++ -std=c++20 -O2 opus.cpp -o opus_bin

#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>
#include <map>
#include <nlohmann/json.hpp>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

using json = nlohmann::json;

namespace mahjong {

constexpr std::array<char, 4> SUITS = {'m', 'p', 's', 'z'};
const std::set<int> TERMINAL_KINDS = {0, 8, 9, 17, 18, 26};
const std::set<int> WIND_KINDS = {27, 28, 29, 30};
const std::set<int> DRAGON_KINDS = {31, 32, 33};
const std::set<int> HONOR_KINDS = {27, 28, 29, 30, 31, 32, 33};
const std::set<int> YAOCHUU_KINDS = {0,  8,  9,  17, 18, 26, 27,
                                     28, 29, 30, 31, 32, 33};
const std::set<int> GREEN_KINDS = {19, 20, 21, 23, 25, 32};

int wind_to_kind34(const std::string& w) {
  if (w == "E") return 27;
  if (w == "S") return 28;
  if (w == "W") return 29;
  if (w == "N") return 30;
  return 27;
}

int wind_to_idx(const std::string& w) {
  if (w == "E") return 0;
  if (w == "S") return 1;
  if (w == "W") return 2;
  return 3;
}

bool is_valid_token(const std::string& token) {
  if (token.size() != 2) return false;
  char num_ch = token[0];
  char suit_ch = token[1];
  if (suit_ch == 'm' || suit_ch == 'p' || suit_ch == 's') {
    return num_ch >= '0' && num_ch <= '9';
  }
  if (suit_ch == 'z') {
    return num_ch >= '1' && num_ch <= '7';
  }
  return false;
}

bool is_aka_token(const std::string& token) {
  return token == "0m" || token == "0p" || token == "0s";
}

int token_to_kind34(const std::string& token) {
  char num_ch = token[0];
  char suit_ch = token[1];
  int suit_idx = 0;
  for (int i = 0; i < 4; ++i) {
    if (SUITS[i] == suit_ch) {
      suit_idx = i;
      break;
    }
  }
  int num = (num_ch == '0') ? 5 : (num_ch - '0');
  return suit_idx * 9 + (num - 1);
}

std::string kind34_to_token(int kind34) {
  int suit_idx = kind34 / 9;
  int num = (kind34 % 9) + 1;
  return std::string(1, static_cast<char>('0' + num)) + SUITS[suit_idx];
}

int dora_from_indicator_kind34(int indicator_kind34) {
  if (indicator_kind34 < 27) {
    int suit_base = (indicator_kind34 / 9) * 9;
    int offset = (indicator_kind34 - suit_base + 1) % 9;
    return suit_base + offset;
  }
  if (indicator_kind34 < 31) {
    return 27 + ((indicator_kind34 - 27 + 1) % 4);
  }
  return 31 + ((indicator_kind34 - 31 + 1) % 3);
}

struct RuleConfig {
  bool kuitan = true;
  bool aka_dora = true;
  int start_points = 25000;
  int return_points = 30000;
  std::string ranking_mode = "plus_minus_zero";

  static RuleConfig from_json(const json& j) {
    RuleConfig r;
    if (!j.is_object()) return r;
    if (j.contains("kuitan")) r.kuitan = j["kuitan"].get<bool>();
    if (j.contains("aka_dora")) r.aka_dora = j["aka_dora"].get<bool>();
    if (j.contains("start_points")) r.start_points = j["start_points"].get<int>();
    if (j.contains("return_points")) r.return_points = j["return_points"].get<int>();
    if (j.contains("ranking_mode")) r.ranking_mode = j["ranking_mode"].get<std::string>();
    return r;
  }
};

struct Meld {
  std::string meld_type;  // "chi", "pon", "daiminkan", "ankan", "kakan"
  std::vector<std::string> tiles;
  std::string called_tile;
  int from_seat = -1;

  bool is_open() const { return meld_type != "ankan"; }
  bool is_kan() const {
    return meld_type == "daiminkan" || meld_type == "ankan" ||
           meld_type == "kakan";
  }
};

std::set<int> get_kuikae_forbidden_kinds(
    const std::string& meld_type, const std::string& called_token,
    const std::vector<std::string>& hand_tokens) {
  int called_kind = token_to_kind34(called_token);
  std::set<int> forbidden = {called_kind};
  if (meld_type == "chi" && hand_tokens.size() == 2) {
    int k1 = token_to_kind34(hand_tokens[0]);
    int k2 = token_to_kind34(hand_tokens[1]);
    if (k1 > k2) std::swap(k1, k2);
    int suit_base = (called_kind / 9) * 9;
    if (k2 - k1 == 1) {
      if (called_kind == k1 - 1 && (k2 + 1) < suit_base + 9) {
        forbidden.insert(k2 + 1);
      } else if (called_kind == k2 + 1 && (k1 - 1) >= suit_base) {
        forbidden.insert(k1 - 1);
      }
    }
  }
  return forbidden;
}

std::array<int, 34> tokens_to_counts34(const std::vector<std::string>& tokens) {
  std::array<int, 34> counts{};
  for (const auto& t : tokens) {
    counts[token_to_kind34(t)] += 1;
  }
  return counts;
}

bool can_form_mentsu(std::array<int, 34>& counts, int idx, int remaining_mentsu) {
  while (idx < 34 && counts[idx] == 0) ++idx;
  if (idx >= 34) return remaining_mentsu == 0;
  if (remaining_mentsu <= 0) return false;

  if (counts[idx] >= 3) {
    counts[idx] -= 3;
    if (can_form_mentsu(counts, idx, remaining_mentsu - 1)) {
      counts[idx] += 3;
      return true;
    }
    counts[idx] += 3;
  }

  if (idx < 27 && (idx % 9) <= 6 && counts[idx + 1] > 0 && counts[idx + 2] > 0) {
    counts[idx] -= 1;
    counts[idx + 1] -= 1;
    counts[idx + 2] -= 1;
    if (can_form_mentsu(counts, idx, remaining_mentsu - 1)) {
      counts[idx] += 1;
      counts[idx + 1] += 1;
      counts[idx + 2] += 1;
      return true;
    }
    counts[idx] += 1;
    counts[idx + 1] += 1;
    counts[idx + 2] += 1;
  }

  return false;
}

bool is_agari_counts34(const std::array<int, 34>& counts34, int num_melds = 0) {
  int total_closed = 0;
  for (int c : counts34) total_closed += c;
  if (total_closed != 14 - 3 * num_melds) return false;

  if (num_melds == 0) {
    bool all_yaochuu = true;
    int yaochuu_sum = 0;
    for (int k : YAOCHUU_KINDS) {
      if (counts34[k] < 1) all_yaochuu = false;
      yaochuu_sum += counts34[k];
    }
    if (all_yaochuu && yaochuu_sum == 14) return true;

    int pairs = 0;
    for (int c : counts34) {
      if (c == 2) ++pairs;
    }
    if (pairs == 7) return true;
  }

  std::array<int, 34> work = counts34;
  for (int k = 0; k < 34; ++k) {
    if (work[k] >= 2) {
      work[k] -= 2;
      if (can_form_mentsu(work, 0, 4 - num_melds)) {
        work[k] += 2;
        return true;
      }
      work[k] += 2;
    }
  }
  return false;
}

std::set<int> get_tenpai_waits(const std::vector<std::string>& closed_tokens_13,
                               const std::vector<Meld>& melds) {
  int num_melds = static_cast<int>(melds.size());
  if (static_cast<int>(closed_tokens_13.size()) != 13 - 3 * num_melds) {
    return {};
  }
  auto counts = tokens_to_counts34(closed_tokens_13);
  std::set<int> waits;
  for (int w = 0; w < 34; ++w) {
    if (counts[w] >= 4) continue;
    counts[w] += 1;
    if (is_agari_counts34(counts, num_melds)) {
      waits.insert(w);
    }
    counts[w] -= 1;
  }
  return waits;
}

bool can_ankan_in_riichi(const std::vector<std::string>& closed_tokens_13,
                         const std::string& drawn_token,
                         const std::vector<Meld>& melds,
                         const std::string& kan_token) {
  int drawn_kind = token_to_kind34(drawn_token);
  int kan_kind = token_to_kind34(kan_token);
  if (kan_kind != drawn_kind) return false;

  auto counts_before = tokens_to_counts34(closed_tokens_13);
  if (counts_before[kan_kind] != 3) return false;

  auto waits_before = get_tenpai_waits(closed_tokens_13, melds);
  if (waits_before.empty() || waits_before.count(kan_kind)) return false;

  std::vector<std::string> remaining_tokens;
  for (const auto& t : closed_tokens_13) {
    if (token_to_kind34(t) != kan_kind) remaining_tokens.push_back(t);
  }
  std::vector<Meld> melds_after = melds;
  std::string tok = kind34_to_token(kan_kind);
  melds_after.push_back(Meld{"ankan", {tok, tok, tok, tok}, "", -1});
  auto waits_after = get_tenpai_waits(remaining_tokens, melds_after);
  return waits_before == waits_after;
}

// ---------------- Agari / Yaku Evaluator ----------------

struct MentsuPart {
  std::string kind;  // "shuntsu", "koutsu", "kantsu"
  std::array<int, 3> tiles34;
  bool is_open;
};

struct Decomposition {
  std::string shape;  // "standard", "chiitoitsu", "kokushi"
  int jantou = 0;
  std::vector<MentsuPart> mentsu;
  std::string wait_type;
};

struct WinContext {
  bool is_tsumo = false;
  std::string winning_token;
  int seat_wind_kind34 = 27;
  int round_wind_kind34 = 27;
  bool is_dealer = false;
  bool is_riichi = false;
  bool is_double_riichi = false;
  bool is_ippatsu = false;
  bool is_rinshan = false;
  bool is_chankan = false;
  bool is_haitei = false;
  bool is_houtei = false;
  bool is_first_uncalled_turn = false;
  std::vector<std::string> dora_indicators;
  std::vector<std::string> ura_dora_indicators;
  RuleConfig rules;
};

struct AgariResult {
  bool is_valid = false;
  int han = 0;
  int fu = 0;
  std::vector<std::pair<std::string, int>> yaku;
  std::vector<std::string> yakuman;
  int base_points = 0;
  int total_points = 0;
};

using RawMentsu = std::pair<std::string, std::array<int, 3>>;

void find_closed_mentsu(std::array<int, 34>& counts, int idx, int needed,
                        std::vector<RawMentsu>& current,
                        std::vector<std::vector<RawMentsu>>& results) {
  while (idx < 34 && counts[idx] == 0) ++idx;
  if (idx >= 34) {
    if (needed == 0) results.push_back(current);
    return;
  }
  if (needed <= 0) return;

  if (counts[idx] >= 3) {
    counts[idx] -= 3;
    current.push_back({"koutsu", {idx, idx, idx}});
    find_closed_mentsu(counts, idx, needed - 1, current, results);
    current.pop_back();
    counts[idx] += 3;
  }

  if (idx < 27 && (idx % 9) <= 6 && counts[idx + 1] > 0 && counts[idx + 2] > 0) {
    counts[idx] -= 1;
    counts[idx + 1] -= 1;
    counts[idx + 2] -= 1;
    current.push_back({"shuntsu", {idx, idx + 1, idx + 2}});
    find_closed_mentsu(counts, idx, needed - 1, current, results);
    current.pop_back();
    counts[idx] += 1;
    counts[idx + 1] += 1;
    counts[idx + 2] += 1;
  }
}

std::vector<Decomposition> enumerate_decompositions(
    const std::vector<std::string>& closed_tokens_13,
    const std::string& winning_token, const std::vector<Meld>& melds,
    bool is_tsumo) {
  int win_kind = token_to_kind34(winning_token);
  auto counts13 = tokens_to_counts34(closed_tokens_13);
  if (counts13[win_kind] >= 4) return {};

  auto counts14 = counts13;
  counts14[win_kind] += 1;
  int num_melds = static_cast<int>(melds.size());
  int sum14 = 0;
  for (int c : counts14) sum14 += c;
  if (sum14 != 14 - 3 * num_melds) return {};

  std::vector<Decomposition> decomps;

  if (num_melds == 0) {
    bool all_yaochuu = true;
    int yaochuu_sum = 0;
    bool is_13_way = true;
    int pair_kind = -1;
    for (int k : YAOCHUU_KINDS) {
      if (counts14[k] < 1) all_yaochuu = false;
      if (counts13[k] != 1) is_13_way = false;
      if (counts14[k] == 2) pair_kind = k;
      yaochuu_sum += counts14[k];
    }
    if (all_yaochuu && yaochuu_sum == 14) {
      decomps.push_back(Decomposition{
          "kokushi", pair_kind, {}, is_13_way ? "kokushi13" : "tanki"});
    }

    int pairs = 0;
    for (int c : counts14) {
      if (c == 2) ++pairs;
    }
    if (pairs == 7) {
      decomps.push_back(Decomposition{"chiitoitsu", win_kind, {}, "tanki"});
    }
  }

  std::vector<MentsuPart> fixed_meld_parts;
  for (const auto& m : melds) {
    int k0 = token_to_kind34(m.tiles[0]);
    if (m.meld_type == "chi") {
      std::array<int, 3> ks = {token_to_kind34(m.tiles[0]),
                               token_to_kind34(m.tiles[1]),
                               token_to_kind34(m.tiles[2])};
      std::sort(ks.begin(), ks.end());
      fixed_meld_parts.push_back(MentsuPart{"shuntsu", ks, true});
    } else if (m.meld_type == "pon") {
      fixed_meld_parts.push_back(MentsuPart{"koutsu", {k0, k0, k0}, true});
    } else {
      fixed_meld_parts.push_back(
          MentsuPart{"kantsu", {k0, k0, k0}, m.is_open()});
    }
  }

  std::set<std::pair<int, std::vector<RawMentsu>>> seen_partitions;
  auto work = counts14;
  for (int jantou = 0; jantou < 34; ++jantou) {
    if (work[jantou] < 2) continue;
    work[jantou] -= 2;
    std::vector<RawMentsu> cur;
    std::vector<std::vector<RawMentsu>> raw_lists;
    find_closed_mentsu(work, 0, 4 - num_melds, cur, raw_lists);
    work[jantou] += 2;

    for (auto canonical : raw_lists) {
      std::sort(canonical.begin(), canonical.end());
      auto key = std::make_pair(jantou, canonical);
      if (seen_partitions.count(key)) continue;
      seen_partitions.insert(key);

      if (jantou == win_kind) {
        std::vector<MentsuPart> parts;
        for (const auto& [mtype, mtiles] : canonical) {
          parts.push_back(MentsuPart{mtype, mtiles, false});
        }
        parts.insert(parts.end(), fixed_meld_parts.begin(),
                     fixed_meld_parts.end());
        decomps.push_back(Decomposition{"standard", jantou, parts, "tanki"});
      }

      for (size_t i = 0; i < canonical.size(); ++i) {
        const auto& [mtype, mtiles] = canonical[i];
        if (mtiles[0] != win_kind && mtiles[1] != win_kind &&
            mtiles[2] != win_kind) {
          continue;
        }
        if (mtype == "koutsu") {
          std::vector<MentsuPart> parts;
          for (size_t j = 0; j < canonical.size(); ++j) {
            bool is_open_part = (j == i) && (!is_tsumo);
            parts.push_back(
                MentsuPart{canonical[j].first, canonical[j].second, is_open_part});
          }
          parts.insert(parts.end(), fixed_meld_parts.begin(),
                       fixed_meld_parts.end());
          decomps.push_back(Decomposition{"standard", jantou, parts, "shabo"});
        } else if (mtype == "shuntsu") {
          int k_low = mtiles[0], k_mid = mtiles[1], k_high = mtiles[2];
          std::string wtype;
          if (win_kind == k_mid) {
            wtype = "kanchan";
          } else if ((win_kind == k_high && (k_low % 9) == 0) ||
                     (win_kind == k_low && (k_high % 9) == 8)) {
            wtype = "penchan";
          } else {
            wtype = "ryanmen";
          }
          std::vector<MentsuPart> parts;
          for (const auto& [mt, mti] : canonical) {
            parts.push_back(MentsuPart{mt, mti, false});
          }
          parts.insert(parts.end(), fixed_meld_parts.begin(),
                       fixed_meld_parts.end());
          decomps.push_back(Decomposition{"standard", jantou, parts, wtype});
        }
      }
    }
  }

  return decomps;
}

bool is_pinfu(const Decomposition& decomp, bool is_menzen,
              const WinContext& ctx) {
  if (!is_menzen || decomp.shape != "standard") return false;
  if (decomp.wait_type != "ryanmen") return false;
  if (DRAGON_KINDS.count(decomp.jantou) ||
      decomp.jantou == ctx.round_wind_kind34 ||
      decomp.jantou == ctx.seat_wind_kind34) {
    return false;
  }
  for (const auto& m : decomp.mentsu) {
    if (m.kind != "shuntsu") return false;
  }
  return true;
}

int calculate_fu(const Decomposition& decomp, bool is_menzen,
                 const WinContext& ctx, bool pinfu) {
  if (decomp.shape == "chiitoitsu") return 25;
  if (decomp.shape == "kokushi") return 30;
  if (pinfu) return ctx.is_tsumo ? 20 : 30;

  int fu = 20;
  if (is_menzen && !ctx.is_tsumo) fu += 10;
  if (ctx.is_tsumo) fu += 2;

  if (DRAGON_KINDS.count(decomp.jantou)) fu += 2;
  if (decomp.jantou == ctx.round_wind_kind34) fu += 2;
  if (decomp.jantou == ctx.seat_wind_kind34) fu += 2;

  if (decomp.wait_type == "kanchan" || decomp.wait_type == "penchan" ||
      decomp.wait_type == "tanki") {
    fu += 2;
  }

  for (const auto& m : decomp.mentsu) {
    if (m.kind == "shuntsu") continue;
    bool is_yaochuu = YAOCHUU_KINDS.count(m.tiles34[0]) > 0;
    if (m.kind == "koutsu") {
      int base = is_yaochuu ? 4 : 2;
      fu += m.is_open ? base : base * 2;
    } else if (m.kind == "kantsu") {
      int base = is_yaochuu ? 16 : 8;
      fu += m.is_open ? base : base * 2;
    }
  }

  if (!is_menzen && fu == 20) return 30;
  return ((fu + 9) / 10) * 10;
}

std::vector<std::string> evaluate_yakuman(
    const std::vector<std::string>& closed_tokens_13,
    const std::vector<int>& all_kinds34, const std::vector<Meld>& melds,
    const Decomposition& decomp, const WinContext& ctx) {
  std::vector<std::string> yakuman_list;
  int num_melds = static_cast<int>(melds.size());

  if (ctx.is_first_uncalled_turn && ctx.is_tsumo && num_melds == 0) {
    yakuman_list.push_back(ctx.is_dealer ? "天和" : "地和");
  }

  if (decomp.shape == "kokushi") {
    yakuman_list.push_back(decomp.wait_type == "kokushi13" ? "国士無双13面"
                                                           : "国士無双");
    return yakuman_list;
  }

  bool all_honors = true;
  for (int k : all_kinds34) {
    if (!HONOR_KINDS.count(k)) all_honors = false;
  }
  if (all_honors) yakuman_list.push_back("字一色");

  if (decomp.shape == "chiitoitsu") return yakuman_list;

  int ankou_count = 0;
  int dragon_triplets = 0;
  int wind_triplets = 0;
  int kan_count = 0;
  for (const auto& m : decomp.mentsu) {
    if (m.kind == "koutsu" || m.kind == "kantsu") {
      if (!m.is_open) ++ankou_count;
      if (DRAGON_KINDS.count(m.tiles34[0])) ++dragon_triplets;
      if (WIND_KINDS.count(m.tiles34[0])) ++wind_triplets;
      if (m.kind == "kantsu") ++kan_count;
    }
  }

  if (ankou_count == 4) {
    yakuman_list.push_back(decomp.wait_type == "tanki" ? "四暗刻単騎" : "四暗刻");
  }
  if (dragon_triplets == 3) yakuman_list.push_back("大三元");
  if (wind_triplets == 4) {
    yakuman_list.push_back("大四喜");
  } else if (wind_triplets == 3 && WIND_KINDS.count(decomp.jantou)) {
    yakuman_list.push_back("小四喜");
  }

  bool all_green = true;
  bool all_terminals = true;
  for (int k : all_kinds34) {
    if (!GREEN_KINDS.count(k)) all_green = false;
    if (!TERMINAL_KINDS.count(k)) all_terminals = false;
  }
  if (all_green) yakuman_list.push_back("緑一色");
  if (all_terminals) yakuman_list.push_back("清老頭");
  if (kan_count == 4) yakuman_list.push_back("四槓子");

  if (num_melds == 0) {
    std::set<int> suits;
    for (int k : all_kinds34) suits.insert(k / 9);
    if (suits.size() == 1 && *suits.begin() < 3) {
      int suit_base = (*suits.begin()) * 9;
      std::array<int, 9> c14{};
      for (int k : all_kinds34) c14[k - suit_base] += 1;
      constexpr std::array<int, 9> req = {3, 1, 1, 1, 1, 1, 1, 1, 3};
      bool ok = true;
      for (int i = 0; i < 9; ++i) {
        if (c14[i] < req[i]) ok = false;
      }
      if (ok) {
        std::array<int, 9> c13{};
        for (const auto& tok : closed_tokens_13) {
          int k = token_to_kind34(tok);
          if (suit_base <= k && k < suit_base + 9) c13[k - suit_base] += 1;
        }
        yakuman_list.push_back(c13 == req ? "純正九蓮宝燈" : "九蓮宝燈");
      }
    }
  }

  return yakuman_list;
}

std::pair<std::vector<std::pair<std::string, int>>, bool> evaluate_regular_yaku(
    const std::vector<int>& all_kinds34, bool is_menzen,
    const Decomposition& decomp, const WinContext& ctx) {
  std::vector<std::pair<std::string, int>> yaku;
  bool pinfu = is_pinfu(decomp, is_menzen, ctx);

  if (ctx.is_double_riichi) {
    yaku.push_back({"両立直", 2});
  } else if (ctx.is_riichi) {
    yaku.push_back({"立直", 1});
  }
  if (is_menzen && ctx.is_tsumo) yaku.push_back({"門前清自摸和", 1});
  if (ctx.is_chankan) yaku.push_back({"槍槓", 1});
  if (ctx.is_rinshan) yaku.push_back({"嶺上開花", 1});
  if (ctx.is_haitei && !ctx.is_rinshan) yaku.push_back({"海底摸月", 1});
  if (ctx.is_houtei && !ctx.is_chankan) yaku.push_back({"河底撈魚", 1});

  bool all_simples = true;
  bool all_yaochuu = true;
  bool has_honors = false;
  bool has_terminals = false;
  std::set<int> number_suits;
  for (int k : all_kinds34) {
    if (YAOCHUU_KINDS.count(k)) all_simples = false;
    else all_yaochuu = false;
    if (HONOR_KINDS.count(k)) has_honors = true;
    if (TERMINAL_KINDS.count(k)) has_terminals = true;
    if (k < 27) number_suits.insert(k / 9);
  }

  if (all_simples && (is_menzen || ctx.rules.kuitan)) {
    yaku.push_back({"断幺九", 1});
  }
  if (number_suits.size() == 1) {
    if (has_honors) yaku.push_back({"混一色", is_menzen ? 3 : 2});
    else yaku.push_back({"清一色", is_menzen ? 6 : 5});
  }
  if (all_yaochuu && has_honors && has_terminals) {
    yaku.push_back({"混老頭", 2});
  }

  if (decomp.shape == "chiitoitsu") {
    yaku.push_back({"七対子", 2});
    return {yaku, false};
  }
  if (decomp.shape != "standard") return {yaku, false};

  if (pinfu) yaku.push_back({"平和", 1});

  std::vector<int> shuntsu_list;
  std::vector<MentsuPart> koutsu_list;
  for (const auto& m : decomp.mentsu) {
    if (m.kind == "shuntsu") {
      shuntsu_list.push_back(m.tiles34[0]);
    } else {
      koutsu_list.push_back(m);
      int k = m.tiles34[0];
      if (k == ctx.round_wind_kind34) yaku.push_back({"役牌:場風牌", 1});
      if (k == ctx.seat_wind_kind34) yaku.push_back({"役牌:門風牌", 1});
      if (k == 31) yaku.push_back({"役牌:白", 1});
      if (k == 32) yaku.push_back({"役牌:發", 1});
      if (k == 33) yaku.push_back({"役牌:中", 1});
    }
  }

  if (is_menzen && shuntsu_list.size() >= 2) {
    auto s = shuntsu_list;
    std::sort(s.begin(), s.end());
    if (s.size() == 4 && s[0] == s[1] && s[2] == s[3]) {
      yaku.push_back({"二盃口", 3});
    } else {
      bool has_pair = false;
      for (size_t i = 0; i + 1 < s.size(); ++i) {
        if (s[i] == s[i + 1]) has_pair = true;
      }
      if (has_pair) yaku.push_back({"一盃口", 1});
    }
  }

  if (shuntsu_list.size() >= 3) {
    std::set<int> s_set(shuntsu_list.begin(), shuntsu_list.end());
    for (int base = 0; base < 7; ++base) {
      if (s_set.count(base) && s_set.count(base + 9) &&
          s_set.count(base + 18)) {
        yaku.push_back({"三色同順", is_menzen ? 2 : 1});
        break;
      }
    }
    for (int suit_idx = 0; suit_idx < 3; ++suit_idx) {
      int b = suit_idx * 9;
      if (s_set.count(b) && s_set.count(b + 3) && s_set.count(b + 6)) {
        yaku.push_back({"一気通貫", is_menzen ? 2 : 1});
        break;
      }
    }
  }

  if (koutsu_list.size() == 4) yaku.push_back({"対々和", 2});

  int ankou_count = 0;
  int kan_count = 0;
  int dragon_koutsu = 0;
  std::set<int> koutsu_kinds;
  for (const auto& m : koutsu_list) {
    if (!m.is_open) ++ankou_count;
    if (m.kind == "kantsu") ++kan_count;
    if (DRAGON_KINDS.count(m.tiles34[0])) ++dragon_koutsu;
    koutsu_kinds.insert(m.tiles34[0]);
  }
  if (ankou_count == 3) yaku.push_back({"三暗刻", 2});
  if (kan_count == 3) yaku.push_back({"三槓子", 2});
  if (koutsu_list.size() >= 3) {
    for (int num = 0; num < 9; ++num) {
      if (koutsu_kinds.count(num) && koutsu_kinds.count(num + 9) &&
          koutsu_kinds.count(num + 18)) {
        yaku.push_back({"三色同刻", 2});
        break;
      }
    }
  }
  if (dragon_koutsu == 2 && DRAGON_KINDS.count(decomp.jantou)) {
    yaku.push_back({"小三元", 2});
  }

  if (!shuntsu_list.empty()) {
    bool all_parts_term = TERMINAL_KINDS.count(decomp.jantou) > 0;
    bool all_parts_yao = YAOCHUU_KINDS.count(decomp.jantou) > 0;
    for (const auto& m : decomp.mentsu) {
      bool has_t = false, has_y = false;
      for (int k : m.tiles34) {
        if (TERMINAL_KINDS.count(k)) has_t = true;
        if (YAOCHUU_KINDS.count(k)) has_y = true;
      }
      if (!has_t) all_parts_term = false;
      if (!has_y) all_parts_yao = false;
    }
    if (all_parts_term) {
      yaku.push_back({"純全帯幺九", is_menzen ? 3 : 2});
    } else if (all_parts_yao) {
      yaku.push_back({"混全帯幺九", is_menzen ? 2 : 1});
    }
  }

  return {yaku, pinfu};
}

int calculate_base_points(int han, int fu, int yakuman_multiplier = 0) {
  if (yakuman_multiplier > 0) return 8000 * yakuman_multiplier;
  if (han >= 13) return 8000;
  if (han >= 11) return 6000;
  if (han >= 8) return 4000;
  if (han >= 6) return 3000;
  if (han >= 5) return 2000;
  int base = fu * (1 << (han + 2));
  return std::min(base, 2000);
}

int base_to_total_win_points(int base_points, bool is_dealer, bool is_tsumo) {
  auto ceil100 = [](int x) { return ((x + 99) / 100) * 100; };
  if (is_dealer) {
    if (is_tsumo) return ceil100(base_points * 2) * 3;
    return ceil100(base_points * 6);
  } else {
    if (is_tsumo) {
      return ceil100(base_points * 2) + ceil100(base_points) * 2;
    }
    return ceil100(base_points * 4);
  }
}

AgariResult evaluate_agari(const std::vector<std::string>& closed_tokens_13,
                           const std::vector<Meld>& melds,
                           const WinContext& ctx) {
  auto decomps = enumerate_decompositions(closed_tokens_13, ctx.winning_token,
                                          melds, ctx.is_tsumo);
  if (decomps.empty()) return AgariResult{};

  bool is_menzen = true;
  for (const auto& m : melds) {
    if (m.is_open()) is_menzen = false;
  }

  std::vector<std::string> all_tokens = closed_tokens_13;
  all_tokens.push_back(ctx.winning_token);
  for (const auto& m : melds) {
    all_tokens.insert(all_tokens.end(), m.tiles.begin(), m.tiles.end());
  }
  std::vector<int> all_kinds34;
  all_kinds34.reserve(all_tokens.size());
  for (const auto& t : all_tokens) all_kinds34.push_back(token_to_kind34(t));

  int dora_count = 0;
  for (const auto& ind : ctx.dora_indicators) {
    int dk = dora_from_indicator_kind34(token_to_kind34(ind));
    dora_count += static_cast<int>(
        std::count(all_kinds34.begin(), all_kinds34.end(), dk));
  }
  int aka_dora_count = 0;
  if (ctx.rules.aka_dora) {
    for (const auto& t : all_tokens) {
      if (is_aka_token(t)) ++aka_dora_count;
    }
  }
  int ura_dora_count = 0;
  if (ctx.is_riichi || ctx.is_double_riichi) {
    for (const auto& ind : ctx.ura_dora_indicators) {
      int uk = dora_from_indicator_kind34(token_to_kind34(ind));
      ura_dora_count += static_cast<int>(
          std::count(all_kinds34.begin(), all_kinds34.end(), uk));
    }
  }

  AgariResult best_result{};

  for (const auto& decomp : decomps) {
    auto yakuman_names = evaluate_yakuman(closed_tokens_13, all_kinds34, melds,
                                          decomp, ctx);
    AgariResult candidate;
    if (!yakuman_names.empty()) {
      int ym_mult = static_cast<int>(yakuman_names.size());
      int base_pts = calculate_base_points(0, 0, ym_mult);
      int total_pts =
          base_to_total_win_points(base_pts, ctx.is_dealer, ctx.is_tsumo);
      int fu = calculate_fu(decomp, is_menzen, ctx, false);
      candidate.is_valid = true;
      candidate.han = 13 * ym_mult;
      candidate.fu = fu;
      for (const auto& ym : yakuman_names) candidate.yaku.push_back({ym, 13});
      candidate.yakuman = yakuman_names;
      candidate.base_points = base_pts;
      candidate.total_points = total_pts;
    } else {
      auto [reg_yaku, pinfu] =
          evaluate_regular_yaku(all_kinds34, is_menzen, decomp, ctx);
      if (reg_yaku.empty()) continue;

      if (ctx.is_ippatsu && !ctx.is_rinshan) reg_yaku.push_back({"一発", 1});
      if (dora_count > 0) reg_yaku.push_back({"ドラ", dora_count});
      if (aka_dora_count > 0) reg_yaku.push_back({"赤ドラ", aka_dora_count});
      if (ura_dora_count > 0) reg_yaku.push_back({"裏ドラ", ura_dora_count});

      int han = 0;
      for (const auto& [_, h] : reg_yaku) han += h;
      int fu = calculate_fu(decomp, is_menzen, ctx, pinfu);
      int base_pts = calculate_base_points(han, fu, 0);
      int total_pts =
          base_to_total_win_points(base_pts, ctx.is_dealer, ctx.is_tsumo);
      candidate.is_valid = true;
      candidate.han = han;
      candidate.fu = fu;
      candidate.yaku = reg_yaku;
      candidate.base_points = base_pts;
      candidate.total_points = total_pts;
    }

    if (!best_result.is_valid ||
        std::tie(candidate.total_points, candidate.han, candidate.fu) >
            std::tie(best_result.total_points, best_result.han,
                     best_result.fu)) {
      best_result = candidate;
    }
  }

  return best_result;
}

std::array<int, 4> compute_score_deltas(int winner_seat, int loser_seat,
                                        int dealer_seat,
                                        const AgariResult& agari, int honba,
                                        int kyoutaku) {
  std::array<int, 4> deltas = {0, 0, 0, 0};
  bool is_dealer = (winner_seat == dealer_seat);
  bool is_tsumo = (loser_seat < 0);
  int B = agari.base_points;
  auto ceil100 = [](int x) { return ((x + 99) / 100) * 100; };

  if (is_tsumo) {
    if (is_dealer) {
      int each = ceil100(B * 2) + 100 * honba;
      for (int s = 0; s < 4; ++s) {
        if (s == winner_seat) continue;
        deltas[s] -= each;
        deltas[winner_seat] += each;
      }
    } else {
      int oya_pay = ceil100(B * 2) + 100 * honba;
      int ko_pay = ceil100(B) + 100 * honba;
      for (int s = 0; s < 4; ++s) {
        if (s == winner_seat) continue;
        int pay = (s == dealer_seat) ? oya_pay : ko_pay;
        deltas[s] -= pay;
        deltas[winner_seat] += pay;
      }
    }
  } else {
    int win_pts = agari.total_points + 300 * honba;
    deltas[loser_seat] -= win_pts;
    deltas[winner_seat] += win_pts;
  }
  deltas[winner_seat] += 1000 * kyoutaku;
  return deltas;
}

// ---------------- Shanten Calculator ----------------

using Vec5 = std::array<int, 5>;
using Suit9 = std::array<int, 9>;
using Honor7 = std::array<int, 7>;

std::map<Suit9, Vec5> suit_no_pair_cache;
std::map<Suit9, std::pair<Vec5, Vec5>> suit_states_cache;

Vec5 suit_no_pair_best_t(const Suit9& c) {
  auto it = suit_no_pair_cache.find(c);
  if (it != suit_no_pair_cache.end()) return it->second;

  int idx = -1;
  for (int i = 0; i < 9; ++i) {
    if (c[i] > 0) {
      idx = i;
      break;
    }
  }
  if (idx == -1) {
    Vec5 res = {0, -1, -1, -1, -1};
    suit_no_pair_cache[c] = res;
    return res;
  }

  Suit9 c_list = c;
  c_list[idx] -= 1;
  Vec5 best = suit_no_pair_best_t(c_list);

  if (c[idx] >= 3) {
    c_list = c;
    c_list[idx] -= 3;
    Vec5 sub = suit_no_pair_best_t(c_list);
    for (int m = 0; m < 4; ++m) {
      if (sub[m] >= 0 && sub[m] > best[m + 1]) best[m + 1] = sub[m];
    }
  }

  if (idx <= 6 && c[idx + 1] > 0 && c[idx + 2] > 0) {
    c_list = c;
    c_list[idx] -= 1;
    c_list[idx + 1] -= 1;
    c_list[idx + 2] -= 1;
    Vec5 sub = suit_no_pair_best_t(c_list);
    for (int m = 0; m < 4; ++m) {
      if (sub[m] >= 0 && sub[m] > best[m + 1]) best[m + 1] = sub[m];
    }
  }

  if (c[idx] >= 2) {
    c_list = c;
    c_list[idx] -= 2;
    Vec5 sub = suit_no_pair_best_t(c_list);
    for (int m = 0; m < 5; ++m) {
      if (sub[m] >= 0 && sub[m] + 1 > best[m]) best[m] = sub[m] + 1;
    }
  }

  if (idx <= 7 && c[idx + 1] > 0) {
    c_list = c;
    c_list[idx] -= 1;
    c_list[idx + 1] -= 1;
    Vec5 sub = suit_no_pair_best_t(c_list);
    for (int m = 0; m < 5; ++m) {
      if (sub[m] >= 0 && sub[m] + 1 > best[m]) best[m] = sub[m] + 1;
    }
  }

  if (idx <= 6 && c[idx + 2] > 0) {
    c_list = c;
    c_list[idx] -= 1;
    c_list[idx + 2] -= 1;
    Vec5 sub = suit_no_pair_best_t(c_list);
    for (int m = 0; m < 5; ++m) {
      if (sub[m] >= 0 && sub[m] + 1 > best[m]) best[m] = sub[m] + 1;
    }
  }

  suit_no_pair_cache[c] = best;
  return best;
}

std::pair<Vec5, Vec5> suit_states(const Suit9& c) {
  auto it = suit_states_cache.find(c);
  if (it != suit_states_cache.end()) return it->second;

  Vec5 no_pair = suit_no_pair_best_t(c);
  Vec5 with_pair = {-1, -1, -1, -1, -1};
  for (int i = 0; i < 9; ++i) {
    if (c[i] >= 2) {
      Suit9 c_list = c;
      c_list[i] -= 2;
      Vec5 sub = suit_no_pair_best_t(c_list);
      for (int m = 0; m < 5; ++m) {
        if (sub[m] > with_pair[m]) with_pair[m] = sub[m];
      }
    }
  }
  auto res = std::make_pair(no_pair, with_pair);
  suit_states_cache[c] = res;
  return res;
}

std::pair<Vec5, Vec5> honor_states(const Honor7& c) {
  int trips = 0, pairs = 0;
  for (int x : c) {
    if (x >= 3) ++trips;
    else if (x == 2) ++pairs;
  }
  Vec5 no_pair = {-1, -1, -1, -1, -1};
  for (int used_m = 0; used_m <= trips; ++used_m) {
    int leftover_pairs = pairs + (trips - used_m);
    if (used_m <= 4) no_pair[used_m] = leftover_pairs;
  }

  Vec5 with_pair = {-1, -1, -1, -1, -1};
  for (int i = 0; i < 7; ++i) {
    if (c[i] >= 2) {
      Honor7 c_list = c;
      c_list[i] -= 2;
      int t2 = 0, p2 = 0;
      for (int x : c_list) {
        if (x >= 3) ++t2;
        else if (x == 2) ++p2;
      }
      for (int used_m = 0; used_m <= t2; ++used_m) {
        if (used_m <= 4) {
          int rem_p = p2 + (t2 - used_m);
          if (rem_p > with_pair[used_m]) with_pair[used_m] = rem_p;
        }
      }
    }
  }
  return {no_pair, with_pair};
}

int calculate_shanten(const std::array<int, 34>& counts34, int num_melds = 0) {
  int need_melds = 4 - num_melds;
  Suit9 cm, cp, cs;
  Honor7 cz;
  for (int i = 0; i < 9; ++i) {
    cm[i] = counts34[i];
    cp[i] = counts34[9 + i];
    cs[i] = counts34[18 + i];
  }
  for (int i = 0; i < 7; ++i) cz[i] = counts34[27 + i];

  auto s_m = suit_states(cm);
  auto s_p = suit_states(cp);
  auto s_s = suit_states(cs);
  auto s_z = honor_states(cz);

  Vec5 cur_no = s_m.first;
  Vec5 cur_yes = s_m.second;

  for (const auto& [nxt_no, nxt_yes] : {s_p, s_s, s_z}) {
    Vec5 merged_no = {-1, -1, -1, -1, -1};
    Vec5 merged_yes = {-1, -1, -1, -1, -1};
    for (int m1 = 0; m1 <= need_melds; ++m1) {
      if (cur_no[m1] < 0 && cur_yes[m1] < 0) continue;
      for (int m2 = 0; m2 <= need_melds - m1; ++m2) {
        int m_tot = m1 + m2;
        if (cur_no[m1] >= 0 && nxt_no[m2] >= 0) {
          int v = cur_no[m1] + nxt_no[m2];
          if (v > merged_no[m_tot]) merged_no[m_tot] = v;
        }
        if (cur_yes[m1] >= 0 && nxt_no[m2] >= 0) {
          int v = cur_yes[m1] + nxt_no[m2];
          if (v > merged_yes[m_tot]) merged_yes[m_tot] = v;
        }
        if (cur_no[m1] >= 0 && nxt_yes[m2] >= 0) {
          int v = cur_no[m1] + nxt_yes[m2];
          if (v > merged_yes[m_tot]) merged_yes[m_tot] = v;
        }
      }
    }
    cur_no = merged_no;
    cur_yes = merged_yes;
  }

  int max_score = 0;
  for (int m = 0; m <= need_melds; ++m) {
    if (cur_no[m] >= 0) {
      int t = std::min(need_melds - m, cur_no[m]);
      int sc = 2 * m + t;
      if (sc > max_score) max_score = sc;
    }
    if (cur_yes[m] >= 0) {
      int t = std::min(need_melds - m, cur_yes[m]);
      int sc = 2 * m + t + 1;
      if (sc > max_score) max_score = sc;
    }
  }

  int best_shanten = 8 - 2 * num_melds - max_score;

  if (num_melds == 0) {
    int pairs = 0, kinds = 0;
    for (int c : counts34) {
      if (c >= 2) ++pairs;
      if (c >= 1) ++kinds;
    }
    int chiitoi_shanten = 6 - pairs + std::max(0, 7 - kinds);
    if (chiitoi_shanten < best_shanten) best_shanten = chiitoi_shanten;

    int yaochuu_kinds = 0;
    int yaochuu_pair = 0;
    for (int k : YAOCHUU_KINDS) {
      if (counts34[k] >= 1) ++yaochuu_kinds;
      if (counts34[k] >= 2) yaochuu_pair = 1;
    }
    int kokushi_shanten = 13 - yaochuu_kinds - yaochuu_pair;
    if (kokushi_shanten < best_shanten) best_shanten = kokushi_shanten;
  }

  return best_shanten;
}

// ---------------- PM0 Strategy Client ----------------

int gosha_rokuiri(double pt) {
  long long tenths = std::llround(pt * 10.0);
  long long abs_tenths = std::llabs(tenths);
  long long abs_rounded = (abs_tenths + 4) / 10;
  return tenths >= 0 ? static_cast<int>(abs_rounded)
                     : -static_cast<int>(abs_rounded);
}

class TagPM0Client {
 public:
  void run() {
    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.empty()) continue;
      json msg;
      try {
        msg = json::parse(line);
      } catch (...) {
        continue;
      }
      json resp = handle_message(msg);
      if (!resp.is_null()) {
        std::cout << resp.dump() << "\n" << std::flush;
      }
      if (msg.value("type", "") == "match_end") {
        break;
      }
    }
  }

 private:
  struct SeatState {
    std::string seat_wind = "E";
    std::vector<std::string> hand;
    std::vector<Meld> melds;
    bool is_riichi = false;
    bool is_double_riichi = false;
    bool is_ippatsu = false;
    bool temp_furiten = false;
    bool riichi_furiten = false;
  };

  int kami_seat_ = 0;
  int shimo_seat_ = 1;
  int enemy_kami_ = 2;
  int enemy_shimo_ = 3;
  int dealer_first_seat_ = 0;
  int dealer_seat_ = 0;
  std::string round_wind_ = "E";
  int round_number_ = 1;
  int honba_ = 0;
  int kyoutaku_ = 0;

  std::array<SeatState, 4> seats_;
  std::array<std::vector<std::string>, 4> all_rivers_full_;
  std::array<std::vector<std::string>, 4> all_rivers_;
  std::array<std::vector<std::vector<std::string>>, 4> all_melds_;
  std::array<bool, 4> opp_riichi_ = {false, false, false, false};
  std::array<std::set<int>, 4> safe_after_riichi_;
  int total_kans_ = 0;
  bool uncalled_first_round_ = true;
  std::vector<std::string> dora_indicators_;
  std::array<int, 4> points_ = {25000, 25000, 25000, 25000};
  RuleConfig rules_;

  bool is_our_seat(int s) const {
    return s == kami_seat_ || s == shimo_seat_;
  }

  int partner_of(int s) const {
    return (s == kami_seat_) ? shimo_seat_ : kami_seat_;
  }

  std::set<int> yakuhai_kinds_for(int s) const {
    std::set<int> res = DRAGON_KINDS;
    res.insert(wind_to_kind34(round_wind_));
    res.insert(wind_to_kind34(seats_[s].seat_wind));
    return res;
  }

  std::vector<int> dora_kinds() const {
    std::vector<int> res;
    for (const auto& ind : dora_indicators_) {
      res.push_back(dora_from_indicator_kind34(token_to_kind34(ind)));
    }
    return res;
  }

  std::array<int, 34> visible_counts(
      const std::vector<std::string>& extra_hand,
      int viewer_seat) const {
    std::array<int, 34> vis{};
    for (const auto& t : extra_hand) {
      vis[token_to_kind34(t)]++;
    }
    int mate = partner_of(viewer_seat);
    if (is_our_seat(mate)) {
      for (const auto& t : seats_[mate].hand) {
        vis[token_to_kind34(t)]++;
      }
    }
    for (const auto& ind : dora_indicators_) {
      vis[token_to_kind34(ind)]++;
    }
    for (int s = 0; s < 4; ++s) {
      for (const auto& t : all_rivers_[s]) {
        vis[token_to_kind34(t)]++;
      }
      for (const auto& m_tiles : all_melds_[s]) {
        for (const auto& t : m_tiles) {
          vis[token_to_kind34(t)]++;
        }
      }
    }
    return vis;
  }

  bool is_furiten(int s, const std::vector<std::string>* custom_hand13 = nullptr) const {
    const auto& st = seats_[s];
    if (st.temp_furiten || st.riichi_furiten) return true;
    const auto& h = custom_hand13 ? *custom_hand13 : st.hand;
    auto waits = get_tenpai_waits(h, st.melds);
    if (waits.empty()) return false;
    for (const auto& r_tile : all_rivers_full_[s]) {
      if (waits.count(token_to_kind34(r_tile))) return true;
    }
    return false;
  }

  int wind_index() const {
    if (round_wind_ == "E") return 0;
    if (round_wind_ == "S") return 1;
    if (round_wind_ == "W") return 2;
    return 3;
  }

  std::array<int, 4> rank_seats_for_scores(const std::array<int, 4>& sc) const {
    std::array<int, 4> order = {0, 1, 2, 3};
    std::sort(order.begin(), order.end(), [&](int a, int b) {
      if (sc[a] != sc[b]) return sc[a] > sc[b];
      return ((a - dealer_first_seat_ + 4) % 4) <
             ((b - dealer_first_seat_ + 4) % 4);
    });
    return order;
  }

  int shimo_final_pt(const std::array<int, 4>& sc, int leftover_kyoutaku = 0) const {
    auto order = rank_seats_for_scores(sc);
    std::array<int, 4> final_sc = sc;
    if (leftover_kyoutaku > 0) {
      final_sc[order[0]] += 1000 * leftover_kyoutaku;
    }
    const double uma[4] = {20.0, 10.0, -10.0, -20.0};
    for (int i = 0; i < 4; ++i) {
      if (order[i] == shimo_seat_) {
        double bonus_oka = (i == 0) ? 20.0 : 0.0;
        double pt = (final_sc[shimo_seat_] - 30000) / 1000.0 + uma[i] + bonus_oka;
        return gosha_rokuiri(pt);
      }
    }
    return -100;
  }

  bool should_end_match_after(
      const std::array<int, 4>& sc,
      const std::string& result_type,
      bool renchan) const {
    if (sc[1] < 0 || sc[3] < 0) return true;
    if (result_type == "chombo") return false;

    int w_idx = wind_index();
    if (w_idx == 0 || (w_idx == 1 && round_number_ < 4)) return false;

    auto ranked = rank_seats_for_scores(sc);
    int top_seat = ranked[0];
    bool has_return = false;
    for (int s = 0; s < 4; ++s) {
      if (sc[s] >= 30000) has_return = true;
    }
    bool dealer_is_top_with_return =
        (top_seat == dealer_seat_ && sc[dealer_seat_] >= 30000);

    if (renchan) {
      if ((w_idx == 1 && round_number_ == 4) || w_idx == 2) {
        if (dealer_is_top_with_return) return true;
      }
      return false;
    }

    if (has_return) return true;
    if (w_idx == 2 && round_number_ == 4) return true;
    return false;
  }

  std::pair<int, int> enemy_low_high(const std::array<int, 4>& sc) const {
    int e0 = enemy_kami_;
    int e1 = enemy_shimo_;
    if (sc[e0] < sc[e1]) return {e0, e1};
    if (sc[e1] < sc[e0]) return {e1, e0};
    bool e0_higher = ((e0 - dealer_first_seat_ + 4) % 4) <
                     ((e1 - dealer_first_seat_ + 4) % 4);
    return e0_higher ? std::make_pair(e1, e0) : std::make_pair(e0, e1);
  }

  // Cost of a 4-player score vector relative to achieving PM0 for shimo_seat_.
  // Supports BOTH:
  //   Path A (R2_KAMI_TOP): Kami is 1st (>=30000), Shimo is 2nd ([19500,20500]), both enemies < Shimo.
  //   Path B (R2_ENEMY_TOP): e_high is 1st (>=30000), Shimo is 2nd ([19500,20500]), Kami & e_low < Shimo.
  //   Path C (R3): both enemies > Shimo, Shimo is 3rd ([39500,40500]), Kami < Shimo.
  std::pair<double, double> r2_costs(const std::array<int, 4>& sc) const {
    double shimo_err = 0.0;
    if (sc[shimo_seat_] < 19500) {
      shimo_err = (19500 - sc[shimo_seat_]) / 1000.0 * 1.5 + 1.0;
    } else if (sc[shimo_seat_] > 20500) {
      shimo_err = (sc[shimo_seat_] - 20500) / 1000.0 * 1.5 + 1.0;
    } else {
      shimo_err = 0.12 * std::abs(sc[shimo_seat_] - 20000) / 500.0;
    }

    auto [e_low, e_high] = enemy_low_high(sc);

    // Path A: Kami is 1st (>= 30000), both enemies < Shimo
    double e0_err = 0.0;
    if (sc[enemy_kami_] >= 19500) {
      e0_err = (sc[enemy_kami_] - 19000) / 1000.0 * 1.1;
    } else if (sc[enemy_kami_] >= sc[shimo_seat_] && sc[shimo_seat_] >= 19500) {
      e0_err = (sc[enemy_kami_] - sc[shimo_seat_] + 500) / 1000.0 * 1.2;
    }
    double e1_err = 0.0;
    if (sc[enemy_shimo_] >= 19500) {
      e1_err = (sc[enemy_shimo_] - 19000) / 1000.0 * 1.1;
    } else if (sc[enemy_shimo_] >= sc[shimo_seat_] && sc[shimo_seat_] >= 19500) {
      e1_err = (sc[enemy_shimo_] - sc[shimo_seat_] + 500) / 1000.0 * 1.2;
    }
    double kami_under_err = 0.0;
    if (sc[kami_seat_] < 36000) {
      kami_under_err = (36000 - sc[kami_seat_]) / 1000.0 * 0.65;
    }
    if (sc[kami_seat_] <= sc[shimo_seat_]) {
      kami_under_err += (sc[shimo_seat_] - sc[kami_seat_] + 1000) / 1000.0 * 0.5;
    }
    double cost_kami_top = shimo_err + e0_err + e1_err + kami_under_err;

    // Path B: e_high is 1st (>= 30000), Kami & e_low < Shimo
    double elow_err = 0.0;
    if (sc[e_low] >= 19500) {
      elow_err = (sc[e_low] - 19000) / 1000.0 * 1.1;
    } else if (sc[e_low] >= sc[shimo_seat_] && sc[shimo_seat_] >= 19500) {
      elow_err = (sc[e_low] - sc[shimo_seat_] + 500) / 1000.0 * 1.2;
    }
    double kami_over_err = 0.0;
    if (sc[kami_seat_] >= 19500) {
      kami_over_err = (sc[kami_seat_] - 18500) / 1000.0 * 1.05;
    } else if (sc[kami_seat_] >= sc[shimo_seat_] && sc[shimo_seat_] >= 19500) {
      kami_over_err = (sc[kami_seat_] - sc[shimo_seat_] + 500) / 1000.0 * 1.2;
    }
    double ehigh_under_err = 0.0;
    if (sc[e_high] < 35000) {
      ehigh_under_err = (35000 - sc[e_high]) / 1000.0 * 0.65;
    }
    double enemy_top_bias = (sc[e_high] < 32000 && sc[kami_seat_] >= sc[e_high] - 5000) ? 12.0 : 1.5;
    double cost_enemy_top = shimo_err + elow_err + kami_over_err + ehigh_under_err + enemy_top_bias;

    return {cost_kami_top, cost_enemy_top};
  }

  double team_state_cost(const std::array<int, 4>& sc, int leftover_kyoutaku = 0) const {
    int pt = shimo_final_pt(sc, leftover_kyoutaku);
    if ((sc[1] < 0 || sc[3] < 0) && pt != 0) {
      return 1000.0 + std::abs(pt) * 10.0;
    }

    if (pt == 0) {
      double c = 0.05 * std::abs(sc[shimo_seat_] - 20000) / 500.0;
      int max_sc = std::max({sc[0], sc[1], sc[2], sc[3]});
      if (max_sc < 30000) {
        c += (30000 - max_sc) / 1000.0 * 0.25;
      }
      return c;
    }

    auto [cost_kami_top, cost_enemy_top] = r2_costs(sc);

    auto [e_low, e_high] = enemy_low_high(sc);
    double shimo_r3 = 0.0;
    if (sc[shimo_seat_] < 39500) {
      shimo_r3 = (39500 - sc[shimo_seat_]) / 1000.0 + 0.5;
    } else if (sc[shimo_seat_] > 40500) {
      shimo_r3 = (sc[shimo_seat_] - 40500) / 1000.0 + 0.5;
    }
    double e_under_r3 = 0.0;
    if (sc[e_low] <= 40500) e_under_r3 += (40600 - sc[e_low]) / 1000.0 * 1.1;
    if (sc[e_high] <= 40500) e_under_r3 += (40600 - sc[e_high]) / 1000.0 * 1.1;
    double cost_r3 = shimo_r3 + e_under_r3 + 2.5;

    return std::min({cost_kami_top, cost_enemy_top, cost_r3});
  }

  bool is_kami_top_plan() const {
    auto [cost_kami_top, cost_enemy_top] = r2_costs(points_);
    return cost_kami_top <= cost_enemy_top;
  }

  std::array<int, 4> simulate_win_scores(
      int winner_seat, int loser_seat, const AgariResult& agari) const {
    std::array<int, 4> sc = points_;
    auto deltas = compute_score_deltas(winner_seat, loser_seat, dealer_seat_, agari, honba_, kyoutaku_);
    for (int s = 0; s < 4; ++s) {
      sc[s] += deltas[s];
    }
    return sc;
  }

  bool should_take_win(
      int winner_seat,
      const AgariResult& agari,
      int loser_seat,
      int /*remaining_tiles*/) const {
    if (!agari.is_valid) return false;
    auto sc_after = simulate_win_scores(winner_seat, loser_seat, agari);
    bool renchan = (winner_seat == dealer_seat_);
    std::string rtype = (loser_seat < 0) ? "tsumo" : "ron";
    bool ends = should_end_match_after(sc_after, rtype, renchan);

    int pt_after = shimo_final_pt(sc_after, 0);
    int pt_now = shimo_final_pt(points_, kyoutaku_);

    if (ends) {
      if (pt_after == 0) return true;
      if (pt_now == 0) return false;
      if (wind_index() == 2 && round_number_ == 4 && !renchan) {
        return std::abs(pt_after) < std::abs(pt_now);
      }
      return false;
    }

    if (pt_after == 0 && pt_now != 0) {
      return true;
    }
    if (pt_now == 0 && pt_after != 0) {
      return false;
    }
    if (pt_now == 0 && pt_after == 0) {
      return winner_seat != dealer_seat_;
    }

    double cost_now = team_state_cost(points_, kyoutaku_);
    double cost_after = team_state_cost(sc_after, 0);

    if (winner_seat == shimo_seat_) {
      // Shimocha takes any win that reduces team_state_cost
      return cost_after < cost_now - 0.2;
    }

    // winner_seat == kami_seat_:
    if (loser_seat == shimo_seat_) {
      // Shimocha intentionally dealing into Kamicha to shed points
      return cost_after < cost_now - 0.25;
    }

    // Kamicha Tsumo or Ron off enemy:
    // Allow whenever it improves team_state_cost (which is true in R2_KAMI_TOP when crushing enemies,
    // or in R2_ENEMY_TOP when sniping e_low or shaving shimocha!)
    return cost_after < cost_now - 0.25;
  }

  bool shimo_wants_to_win() const {
    if (points_[shimo_seat_] < 19500) return true;
    auto [e_low, e_high] = enemy_low_high(points_);
    if (points_[shimo_seat_] <= 20500 && shimo_final_pt(points_, kyoutaku_) != 0 &&
        points_[e_low] >= points_[shimo_seat_] && points_[e_low] <= 23000) {
      return true;
    }
    return false;
  }

  bool kami_wants_to_build() const {
    int pt_now = shimo_final_pt(points_, kyoutaku_);
    if (pt_now == 0) {
      // If Shimocha is already at PM0:
      // Non-dealer Kamicha builds a fast hand to advance/end the match (provided Kamicha stays on the same side of Shimocha)!
      if (kami_seat_ != dealer_seat_) {
        if (points_[kami_seat_] >= points_[shimo_seat_] + 1000 ||
            points_[kami_seat_] <= points_[shimo_seat_] - 2500) {
          return true;
        }
      }
      // Or if Kamicha IS dealer in South 4 / West and is 1st place >= 30000, winning ends the match via Agari-yame!
      if (kami_seat_ == dealer_seat_ &&
          ((wind_index() == 1 && round_number_ == 4) || wind_index() == 2) &&
          points_[kami_seat_] >= 30000 &&
          rank_seats_for_scores(points_)[0] == kami_seat_) {
        return true;
      }
      return false;
    }

    // If we are on the R2_KAMI_TOP plan (Kami aims for 1st place >=30000 while crushing enemies <19500):
    if (is_kami_top_plan()) {
      return true;
    }

    // Otherwise we are on the R2_ENEMY_TOP plan (e_high is 1st place, Kami stays < Shimo):
    int team_sum = points_[kami_seat_] + points_[shimo_seat_] + 1000 * kyoutaku_;
    if (points_[shimo_seat_] > 20500 && team_sum <= 39000 &&
        points_[kami_seat_] <= 17500) {
      return true;
    }
    auto [e_low, e_high] = enemy_low_high(points_);
    if (points_[shimo_seat_] >= 19500 && points_[e_low] >= 19500 &&
        points_[kami_seat_] <= 15000) {
      return true;
    }
    return false;
  }

  double tile_danger_against_seat(int k, int opp) const {
    for (const auto& t : all_rivers_full_[opp]) {
      if (token_to_kind34(t) == k) return 0.0;
    }
    if (safe_after_riichi_[opp].count(k)) return 0.0;

    std::set<int> opp_discards;
    for (const auto& t : all_rivers_full_[opp]) {
      opp_discards.insert(token_to_kind34(t));
    }

    auto vis = visible_counts(seats_[kami_seat_].hand, kami_seat_);
    double base = 5.0;
    if (k >= 27) {
      int rem = 4 - vis[k];
      if (rem <= 0) base = 0.0;
      else if (rem == 1) base = 0.8;
      else if (rem == 2) base = 2.5;
      else base = 4.2;
    } else {
      int r = k % 9;
      int s_base = (k / 9) * 9;
      bool suji_left = (r >= 3) ? opp_discards.count(s_base + r - 3) : true;
      bool suji_right = (r <= 5) ? opp_discards.count(s_base + r + 3) : true;
      if (r == 0 || r == 8) {
        base = (suji_left && suji_right) ? 1.2 : 2.8;
      } else if (r == 1 || r == 7) {
        base = (suji_left && suji_right) ? 2.0 : 4.2;
      } else if (r == 2 || r == 6) {
        base = (suji_left && suji_right) ? 2.5 : 5.8;
      } else {
        if (suji_left && suji_right) base = 2.2;
        else if (suji_left || suji_right) base = 5.0;
        else base = 7.8;
      }
    }

    for (int dk : dora_kinds()) {
      if (dk == k) base *= 1.35;
    }
    if (opp == dealer_seat_) base *= 1.2;
    return base;
  }

  double enemy_threat_weight(int opp) const {
    if (opp == kami_seat_ || opp == shimo_seat_) return 0.0;
    if (opp_riichi_[opp]) return 1.0;
    int m_cnt = static_cast<int>(all_melds_[opp].size());
    int r_cnt = static_cast<int>(all_rivers_full_[opp].size());
    if (m_cnt >= 3) return 0.75;
    if (m_cnt == 2) return (r_cnt >= 8) ? 0.50 : 0.30;
    if (m_cnt == 1) return (r_cnt >= 10) ? 0.25 : 0.10;
    if (r_cnt >= 12) return 0.15;
    return 0.0;
  }

  double total_enemy_danger(int k, bool is_aka) const {
    double total = 0.0;
    for (int opp : {enemy_kami_, enemy_shimo_}) {
      double w = enemy_threat_weight(opp);
      if (w <= 0.0) continue;
      double d = tile_danger_against_seat(k, opp);
      if (is_aka && d > 0.0) d *= 1.25;
      total += w * d;
    }
    return total;
  }

  double tile_keep_value_for(
      int s,
      const std::string& token,
      const std::array<int, 34>& counts34) const {
    int k = token_to_kind34(token);
    double val = 0.0;
    bool want_big = (s == kami_seat_ && is_kami_top_plan()) ||
                    (s == shimo_seat_ && points_[shimo_seat_] < 15000);
    if (is_aka_token(token)) val += want_big ? 3.5 : 1.0;
    for (int dk : dora_kinds()) {
      if (k == dk) val += want_big ? 3.0 : 0.8;
    }
    auto yh = yakuhai_kinds_for(s);
    if (yh.count(k)) {
      val += (counts34[k] >= 2) ? 5.0 : 1.3;
    } else if (k >= 27) {
      val += (counts34[k] == 1) ? 0.1 : 1.5;
    } else {
      int num = (k % 9) + 1;
      constexpr double centrality[10] = {0.0, 0.5, 1.4, 2.2, 2.5,
                                         2.6, 2.5, 2.2, 1.4, 0.5};
      val += centrality[num];
      int suit_base = (k / 9) * 9;
      int idx = k - suit_base;
      if (counts34[k] >= 2) val += 2.0;
      if (idx > 0 && counts34[suit_base + idx - 1] > 0) val += 1.8;
      if (idx < 8 && counts34[suit_base + idx + 1] > 0) val += 1.8;
      if (idx > 1 && counts34[suit_base + idx - 2] > 0) val += 0.9;
      if (idx < 7 && counts34[suit_base + idx + 2] > 0) val += 0.9;
    }
    return val;
  }

  // Strict open yaku check on remaining 13/10/7/4-tile hand after discard + melds
  bool has_open_yaku_plan(
      int s,
      const std::vector<std::string>& rem_hand_after_discard,
      const std::vector<Meld>& new_melds) const {
    auto yakuhai = yakuhai_kinds_for(s);
    for (const auto& m : new_melds) {
      if (m.meld_type != "chi") {
        if (yakuhai.count(token_to_kind34(m.tiles[0]))) return true;
      }
    }

    auto counts34 = tokens_to_counts34(rem_hand_after_discard);
    for (int yk : yakuhai) {
      if (counts34[yk] >= 3) return true;
    }

    if (rules_.kuitan) {
      bool melds_all_simples = true;
      for (const auto& m : new_melds) {
        for (const auto& t : m.tiles) {
          if (YAOCHUU_KINDS.count(token_to_kind34(t))) {
            melds_all_simples = false;
          }
        }
      }
      if (melds_all_simples) {
        int hand_yaochuu = 0;
        for (int k : YAOCHUU_KINDS) hand_yaochuu += counts34[k];
        if (hand_yaochuu == 0) return true;
      }
    }

    std::set<int> suits_present;
    for (const auto& m : new_melds) {
      for (const auto& t : m.tiles) {
        int k = token_to_kind34(t);
        if (k < 27) suits_present.insert(k / 9);
      }
    }
    for (int k = 0; k < 27; ++k) {
      if (counts34[k] > 0) suits_present.insert(k / 9);
    }
    if (suits_present.size() == 1) return true;

    return false;
  }

  // Evaluate a 13/10/7/4-tile hand for seat `s`: returns (effective_shanten, efficiency_score, should_riichi)
  std::tuple<int, double, bool> score_hand13_for_seat(
      int s,
      const std::vector<std::string>& hand13,
      const std::vector<Meld>& melds,
      const std::array<int, 34>& vis_counts,
      bool can_riichi,
      int remaining_tiles) const {
    auto counts34 = tokens_to_counts34(hand13);
    int num_melds = static_cast<int>(melds.size());
    int shanten = calculate_shanten(counts34, num_melds);

    int mate = partner_of(s);
    std::array<int, 34> mate_counts{};
    for (const auto& t : seats_[mate].hand) {
      mate_counts[token_to_kind34(t)]++;
    }

    if (shanten == 0) {
      auto waits = get_tenpai_waits(hand13, melds);
      if (waits.empty()) return {1, 0.0, false};

      bool is_furiten_wait = is_furiten(s, &hand13);
      int total_wait_copies = 0;
      double yaku_wait_copies = 0.0;
      double riichi_wait_copies = 0.0;
      int mate_yaku_waits = 0;
      int mate_riichi_waits = 0;

      for (int w : waits) {
        int rem_copies = std::max(0, 4 - vis_counts[w]);
        int in_mate = mate_counts[w];
        total_wait_copies += rem_copies + in_mate;
        std::string w_tok = kind34_to_token(w);

        WinContext ctx_dama;
        ctx_dama.is_tsumo = false;
        ctx_dama.winning_token = w_tok;
        ctx_dama.seat_wind_kind34 = wind_to_kind34(seats_[s].seat_wind);
        ctx_dama.round_wind_kind34 = wind_to_kind34(round_wind_);
        ctx_dama.is_dealer = (s == dealer_seat_);
        ctx_dama.is_riichi = false;
        ctx_dama.dora_indicators = dora_indicators_;
        ctx_dama.rules = rules_;

        auto ag_dama = evaluate_agari(hand13, melds, ctx_dama);
        if (ag_dama.is_valid) {
          yaku_wait_copies += rem_copies + 4.0 * in_mate;
          if (in_mate > 0 && should_take_win(s, ag_dama, mate, remaining_tiles)) {
            mate_yaku_waits += in_mate;
          }
        } else if (can_riichi) {
          WinContext ctx_riichi = ctx_dama;
          ctx_riichi.is_riichi = true;
          auto ag_riichi = evaluate_agari(hand13, melds, ctx_riichi);
          riichi_wait_copies += rem_copies + 4.0 * in_mate;
          if (in_mate > 0 && ag_riichi.is_valid &&
              should_take_win(s, ag_riichi, mate, remaining_tiles)) {
            mate_riichi_waits += in_mate;
          }
        } else {
          WinContext ctx_tsumo = ctx_dama;
          ctx_tsumo.is_tsumo = true;
          if (evaluate_agari(hand13, melds, ctx_tsumo).is_valid) {
            yaku_wait_copies += rem_copies * 0.4;
          }
        }
      }

      double effective_copies = yaku_wait_copies + (can_riichi ? riichi_wait_copies : 0.0);
      if (effective_copies <= 0.0) {
        // Dead tenpai with NO yaku! Treat as shanten 1 so we don't sit in yaku-less tenpai!
        return {1, -500.0, false};
      }

      double furiten_mult = is_furiten_wait ? 0.35 : 1.0;
      double keep_bonus = 0.0;
      for (const auto& t : hand13) keep_bonus += tile_keep_value_for(s, t, counts34);
      keep_bonus *= 0.01;

      double eff_score =
          (effective_copies * 100.0 + total_wait_copies * 10.0) * furiten_mult +
          keep_bonus + 400.0 * (mate_yaku_waits + mate_riichi_waits);

      bool should_riichi = false;
      if (can_riichi && !is_furiten_wait && total_wait_copies > 0) {
        if (yaku_wait_copies == 0.0) {
          // We HAVE to riichi to get a yaku!
          if (s == kami_seat_ && is_kami_top_plan()) {
            should_riichi = true;
          } else if (s == shimo_seat_ && (mate_riichi_waits > 0 || points_[shimo_seat_] < 18500)) {
            should_riichi = true;
          }
        } else if (s == kami_seat_ && is_kami_top_plan() && points_[kami_seat_] < 34000) {
          // Kamicha in R2_KAMI_TOP wants maximum points (riichi + ippatsu + uradora) to climb >= 35000!
          should_riichi = true;
        }
      }
      return {0, eff_score, should_riichi};
    }

    int ukeire_copies = 0;
    for (int k = 0; k < 34; ++k) {
      if (counts34[k] >= 4) continue;
      int rem = std::max(0, 4 - vis_counts[k]);
      int in_mate = mate_counts[k];
      if (rem <= 0 && in_mate <= 0) continue;
      counts34[k] += 1;
      if (calculate_shanten(counts34, num_melds) < shanten) {
        ukeire_copies += rem + 2 * in_mate;
      }
      counts34[k] -= 1;
    }

    double keep_bonus = 0.0;
    for (const auto& t : hand13) keep_bonus += tile_keep_value_for(s, t, counts34);
    keep_bonus *= 0.05;

    // Crucial: if hand is already open (`num_melds > 0`), penalize breaking `has_open_yaku_plan`!
    double open_yaku_penalty = 0.0;
    if (num_melds > 0 && !has_open_yaku_plan(s, hand13, melds)) {
      open_yaku_penalty = 600.0;
    }

    return {shanten, ukeire_copies * 10.0 + keep_bonus - open_yaku_penalty, false};
  }

  static void remove_one_token(std::vector<std::string>& vec, const std::string& val) {
    auto it = std::find(vec.begin(), vec.end(), val);
    if (it != vec.end()) vec.erase(it);
  }

  json consider_call_for_seat(
      int s,
      const std::string& discarded_tile,
      int discarder_seat,
      int remaining_tiles) const {
    const auto& st = seats_[s];
    if (st.is_riichi || remaining_tiles <= 0) return nullptr;

    // If s is kamicha and discarder is an enemy, never steal a tile that shimocha can Ron or wants to call!
    if (s == kami_seat_ && discarder_seat != shimo_seat_) {
      if (!is_furiten(shimo_seat_)) {
        WinContext wctx;
        wctx.is_tsumo = false;
        wctx.winning_token = discarded_tile;
        wctx.seat_wind_kind34 = wind_to_kind34(seats_[shimo_seat_].seat_wind);
        wctx.round_wind_kind34 = wind_to_kind34(round_wind_);
        wctx.is_dealer = (shimo_seat_ == dealer_seat_);
        wctx.dora_indicators = dora_indicators_;
        wctx.rules = rules_;
        auto ag = evaluate_agari(seats_[shimo_seat_].hand, seats_[shimo_seat_].melds, wctx);
        if (ag.is_valid && should_take_win(shimo_seat_, ag, discarder_seat, remaining_tiles)) {
          return nullptr;
        }
      }
      json shimo_call = consider_call_for_seat(shimo_seat_, discarded_tile, discarder_seat, remaining_tiles);
      if (!shimo_call.is_null()) {
        return nullptr;
      }
    }

    // Never let Kamicha call off Shimocha unless Shimocha is > 20500 (shedding points) or already at PM0!
    if (s == kami_seat_ && discarder_seat == shimo_seat_) {
      if (points_[shimo_seat_] <= 20500 && shimo_final_pt(points_, kyoutaku_) != 0) {
        return nullptr;
      }
    }

    bool want_call = (s == shimo_seat_) ? shimo_wants_to_win() : kami_wants_to_build();
    if (!want_call) return nullptr;

    auto vis_counts = visible_counts(st.hand, s);
    int shanten_before = calculate_shanten(
        tokens_to_counts34(st.hand), static_cast<int>(st.melds.size()));
    int k_disc = token_to_kind34(discarded_tile);
    auto yakuhai = yakuhai_kinds_for(s);

    json best_call = nullptr;
    int best_shanten = shanten_before;
    double best_score = -1e9;

    // Candidate 1: Pon
    std::vector<std::string> matching;
    for (const auto& t : st.hand) {
      if (token_to_kind34(t) == k_disc) matching.push_back(t);
    }
    if (matching.size() >= 2) {
      std::stable_sort(matching.begin(), matching.end(),
                       [](const std::string& a, const std::string& b) {
                         return (is_aka_token(a) ? 0 : 1) < (is_aka_token(b) ? 0 : 1);
                       });
      std::vector<std::string> pon_tiles = {matching[0], matching[1]};
      std::vector<std::string> rem_hand = st.hand;
      for (const auto& pt : pon_tiles) remove_one_token(rem_hand, pt);

      auto forb_kinds = get_kuikae_forbidden_kinds("pon", discarded_tile, pon_tiles);
      std::set<std::string> legal_discards;
      for (const auto& t : rem_hand) {
        if (!forb_kinds.count(token_to_kind34(t))) legal_discards.insert(t);
      }
      if (!legal_discards.empty()) {
        std::vector<Meld> new_melds = st.melds;
        new_melds.push_back(
            Meld{"pon", {pon_tiles[0], pon_tiles[1], discarded_tile}, discarded_tile, discarder_seat});
        for (const auto& d : legal_discards) {
          std::vector<std::string> rem_after = rem_hand;
          remove_one_token(rem_after, d);
          if (!has_open_yaku_plan(s, rem_after, new_melds)) continue;
          auto [sh_after, eff_after, _] =
              score_hand13_for_seat(s, rem_after, new_melds, vis_counts, false, remaining_tiles);
          bool allow_equal = (yakuhai.count(k_disc) && shanten_before <= 2);
          if (sh_after < best_shanten ||
              (allow_equal && sh_after == shanten_before && best_call.is_null()) ||
              (sh_after == best_shanten && !best_call.is_null() && eff_after > best_score)) {
            best_shanten = sh_after;
            best_score = eff_after;
            best_call = {{"action", "pon"}, {"tiles", pon_tiles}};
          }
        }
      }
    }

    // Candidate 2: Chi (only from Kamicha of s)
    int max_chi_shanten_after = (s == shimo_seat_ && discarder_seat == kami_seat_) ? 2 : 1;
    if ((discarder_seat + 1) % 4 == s && k_disc < 27) {
      int suit_base = (k_disc / 9) * 9;
      int num = k_disc - suit_base;
      std::vector<std::pair<int, int>> chi_patterns;
      if (num >= 2) chi_patterns.push_back({suit_base + num - 2, suit_base + num - 1});
      if (1 <= num && num <= 7) chi_patterns.push_back({suit_base + num - 1, suit_base + num + 1});
      if (num <= 6) chi_patterns.push_back({suit_base + num + 1, suit_base + num + 2});

      for (const auto& [k1, k2] : chi_patterns) {
        std::vector<std::string> t1_list, t2_list;
        for (const auto& t : st.hand) {
          if (token_to_kind34(t) == k1) t1_list.push_back(t);
          if (token_to_kind34(t) == k2) t2_list.push_back(t);
        }
        if (t1_list.empty() || t2_list.empty()) continue;
        auto aka_cmp = [](const std::string& a, const std::string& b) {
          return (is_aka_token(a) ? 0 : 1) < (is_aka_token(b) ? 0 : 1);
        };
        std::stable_sort(t1_list.begin(), t1_list.end(), aka_cmp);
        std::stable_sort(t2_list.begin(), t2_list.end(), aka_cmp);
        std::vector<std::string> chi_tiles = {t1_list[0], t2_list[0]};
        std::vector<std::string> rem_hand = st.hand;
        for (const auto& ct : chi_tiles) remove_one_token(rem_hand, ct);

        auto forb_kinds = get_kuikae_forbidden_kinds("chi", discarded_tile, chi_tiles);
        std::set<std::string> legal_discards;
        for (const auto& t : rem_hand) {
          if (!forb_kinds.count(token_to_kind34(t))) legal_discards.insert(t);
        }
        if (legal_discards.empty()) continue;

        std::vector<Meld> new_melds = st.melds;
        new_melds.push_back(
            Meld{"chi", {chi_tiles[0], chi_tiles[1], discarded_tile}, discarded_tile, discarder_seat});
        for (const auto& d : legal_discards) {
          std::vector<std::string> rem_after = rem_hand;
          remove_one_token(rem_after, d);
          if (!has_open_yaku_plan(s, rem_after, new_melds)) continue;
          auto [sh_after, eff_after, _] =
              score_hand13_for_seat(s, rem_after, new_melds, vis_counts, false, remaining_tiles);
          if (sh_after < shanten_before && sh_after <= max_chi_shanten_after) {
            if (sh_after < best_shanten ||
                (sh_after == best_shanten && eff_after > best_score)) {
              best_shanten = sh_after;
              best_score = eff_after;
              best_call = {{"action", "chi"}, {"tiles", chi_tiles}};
            }
          }
        }
      }
    }

    return best_call;
  }

  std::pair<std::string, bool> choose_discard_for_seat(
      int s,
      const std::vector<std::string>& full_hand,
      const std::set<int>& forbidden_kinds,
      bool can_declare_riichi,
      int remaining_tiles) const {
    const auto& st = seats_[s];
    std::vector<std::string> legal_tiles;
    std::set<std::string> seen;
    for (const auto& t : full_hand) {
      if (!forbidden_kinds.count(token_to_kind34(t))) {
        if (seen.insert(t).second) {
          legal_tiles.push_back(t);
        }
      }
    }
    if (legal_tiles.empty()) {
      return {full_hand.back(), false};
    }

    // Late-round Noten Riichi Chombo option for Kamicha (4 <= remaining_tiles <= 6)
    if (s == kami_seat_ && can_declare_riichi && remaining_tiles >= 4 &&
        remaining_tiles <= 6 && st.melds.empty() && points_[kami_seat_] >= 1000) {
      std::array<int, 4> sc_chombo = points_;
      if (kami_seat_ == dealer_seat_) {
        for (int i = 0; i < 4; ++i) {
          sc_chombo[i] += (i == kami_seat_) ? -12000 : 4000;
        }
      } else {
        for (int i = 0; i < 4; ++i) {
          if (i == kami_seat_) sc_chombo[i] -= 8000;
          else if (i == dealer_seat_) sc_chombo[i] += 4000;
          else sc_chombo[i] += 2000;
        }
      }
      int pt_chombo = shimo_final_pt(sc_chombo, kyoutaku_);
      int pt_now = shimo_final_pt(points_, kyoutaku_);
      if (pt_chombo == 0 && pt_now != 0) {
        int num_melds = static_cast<int>(st.melds.size());
        std::string best_noten_tile;
        double min_danger = 1e9;
        for (const auto& cand : legal_tiles) {
          std::vector<std::string> h13 = full_hand;
          remove_one_token(h13, cand);
          auto c34 = tokens_to_counts34(h13);
          if (calculate_shanten(c34, num_melds) >= 1) {
            double d = total_enemy_danger(token_to_kind34(cand), is_aka_token(cand));
            if (d < min_danger) {
              min_danger = d;
              best_noten_tile = cand;
            }
          }
        }
        if (!best_noten_tile.empty() && min_danger < 3.0) {
          return {best_noten_tile, true};
        }
      }
    }

    int mate = partner_of(s);
    int num_melds = static_cast<int>(st.melds.size());
    auto vis = visible_counts(full_hand, s);
    auto d_kinds = dora_kinds();
    bool want_build = (s == shimo_seat_) ? shimo_wants_to_win() : kami_wants_to_build();
    auto [e_low, e_high] = enemy_low_high(points_);

    // Late-round Exhaustive Draw Tenpai/Noten coordination (remaining_tiles <= 12)
    bool prefer_noten_at_draw = false;
    bool prefer_tenpai_at_draw = false;
    if (remaining_tiles <= 12) {
      if (s == shimo_seat_) {
        if (points_[shimo_seat_] > 20500) {
          prefer_noten_at_draw = true;
        } else if (points_[shimo_seat_] < 19500) {
          prefer_tenpai_at_draw = true;
        } else if (!opp_riichi_[enemy_kami_] && !opp_riichi_[enemy_shimo_]) {
          prefer_noten_at_draw = true;
        }
      } else {
        // s == kami_seat_:
        if (points_[shimo_seat_] > 20500) {
          // Kami being Tenpai forces Noten Shimo to pay -1000/-1500!
          prefer_tenpai_at_draw = true;
        } else if (points_[shimo_seat_] < 19500) {
          // Kami staying Noten lets Tenpai Shimo collect +1000/+1500!
          prefer_noten_at_draw = true;
        }
      }
    }

    std::string best_tile = legal_tiles.front();
    double best_total_score = -1e18;
    bool best_riichi = false;

    for (const auto& cand : legal_tiles) {
      int k = token_to_kind34(cand);
      bool is_aka = is_aka_token(cand);

      std::vector<std::string> h13 = full_hand;
      remove_one_token(h13, cand);

      auto [eff_shanten, eff_score, should_riichi] =
          score_hand13_for_seat(s, h13, st.melds, vis, can_declare_riichi, remaining_tiles);
      int raw_shanten = calculate_shanten(tokens_to_counts34(h13), num_melds);

      double total_score = 0.0;

      // 1. Direct Ron feeding to our teammate (`mate`)!
      if (!is_furiten(mate)) {
        WinContext wctx;
        wctx.is_tsumo = false;
        wctx.winning_token = cand;
        wctx.seat_wind_kind34 = wind_to_kind34(seats_[mate].seat_wind);
        wctx.round_wind_kind34 = wind_to_kind34(round_wind_);
        wctx.is_dealer = (mate == dealer_seat_);
        wctx.is_riichi = seats_[mate].is_riichi;
        wctx.is_double_riichi = seats_[mate].is_double_riichi;
        wctx.is_ippatsu = seats_[mate].is_ippatsu;
        wctx.is_houtei = (remaining_tiles == 0);
        wctx.dora_indicators = dora_indicators_;
        wctx.rules = rules_;
        auto ag = evaluate_agari(seats_[mate].hand, seats_[mate].melds, wctx);
        if (ag.is_valid && should_take_win(mate, ag, s, remaining_tiles)) {
          auto sc_after = simulate_win_scores(mate, s, ag);
          int pt_after = shimo_final_pt(sc_after, 0);
          double cost_now = team_state_cost(points_, kyoutaku_);
          double cost_after = team_state_cost(sc_after, 0);
          if (pt_after == 0) {
            total_score += 100000.0;
          } else {
            total_score += 20000.0 + 2000.0 * std::max(0.0, cost_now - cost_after);
          }
        }
      }

      // 2. Direct Pon/Chi meld feeding from Kamicha (`kami_seat_`) to Shimocha (`shimo_seat_`)!
      if (s == kami_seat_ && shimo_wants_to_win() && remaining_tiles > 0) {
        json shimo_call = consider_call_for_seat(shimo_seat_, cand, kami_seat_, remaining_tiles);
        if (!shimo_call.is_null()) {
          int shimo_cur_sh = calculate_shanten(
              tokens_to_counts34(seats_[shimo_seat_].hand),
              static_cast<int>(seats_[shimo_seat_].melds.size()));
          total_score += (shimo_cur_sh == 1) ? 4500.0 : 2500.0;
        }
      }

      // 3. Hand building vs defense
      double danger = total_enemy_danger(k, is_aka);
      double danger_elow = tile_danger_against_seat(k, e_low) * enemy_threat_weight(e_low);

      if (prefer_noten_at_draw && raw_shanten == 0) {
        total_score -= 3000.0;
      } else if (prefer_tenpai_at_draw && raw_shanten == 0) {
        total_score += 1500.0;
      }

      if (want_build && !prefer_noten_at_draw) {
        // Use SimpleClient's strong shanten/ukeire hierarchy: (-eff_shanten * 1000.0 + eff_score)
        total_score += -1000.0 * eff_shanten + eff_score;

        double discard_penalty = 0.0;
        if (is_aka) discard_penalty += 2.5;
        if (std::find(d_kinds.begin(), d_kinds.end(), k) != d_kinds.end()) {
          discard_penalty += 2.0;
        }
        total_score -= discard_penalty;

        // Defense against enemy riichi/threats
        double def_mult = (s == shimo_seat_) ? 25.0 : 12.0;
        if (eff_shanten == 0) def_mult *= 0.35;  // Push harder when already tenpai!
        total_score -= def_mult * danger + 15.0 * danger_elow;
      } else {
        // Pure defense / fold mode (e.g. Shimocha >= 19500)
        total_score -= 80.0 * danger + 40.0 * danger_elow;
        if (raw_shanten == 0 && !prefer_tenpai_at_draw) {
          total_score -= 200.0;
        }
        int safe_enemies = 0;
        for (int opp : {enemy_kami_, enemy_shimo_}) {
          if (tile_danger_against_seat(k, opp) == 0.0) ++safe_enemies;
        }
        if (enemy_threat_weight(enemy_kami_) == 0.0 &&
            enemy_threat_weight(enemy_shimo_) == 0.0) {
          // No enemy threat yet: shed dangerous live middle/dora tiles early, keep safe tiles
          double avg_raw = 0.5 * (tile_danger_against_seat(k, enemy_kami_) +
                                  tile_danger_against_seat(k, enemy_shimo_));
          total_score += 4.0 * avg_raw - 5.0 * safe_enemies;
        } else {
          total_score += 12.0 * safe_enemies;
        }
      }

      if (total_score > best_total_score) {
        best_total_score = total_score;
        best_tile = cand;
        best_riichi = should_riichi && want_build && !prefer_noten_at_draw;
      }
    }

    // Also check if Shimocha should declare Tenpai Riichi solely to drop -1000 kyoutaku into [19500, 20500]
    if (!best_riichi && s == shimo_seat_ && can_declare_riichi &&
        wind_index() >= 1 && round_number_ >= 3) {
      std::vector<std::string> h13 = full_hand;
      remove_one_token(h13, best_tile);
      if (calculate_shanten(tokens_to_counts34(h13), num_melds) == 0) {
        std::array<int, 4> sc_riichi = points_;
        sc_riichi[shimo_seat_] -= 1000;
        if (shimo_final_pt(points_, kyoutaku_) != 0 &&
            shimo_final_pt(sc_riichi, kyoutaku_ + 1) == 0) {
          best_riichi = true;
        }
      }
    }

    return {best_tile, best_riichi};
  }

  json handle_message(const json& msg) {
    std::string msg_type = msg.value("type", "");
    int seq = msg.value("seq", 0);

    if (msg_type == "match_start") {
      int s = msg.value("target_seat", msg.value("seat", 0));
      kami_seat_ = (s / 2) * 2;
      shimo_seat_ = kami_seat_ + 1;
      enemy_kami_ = (kami_seat_ + 2) % 4;
      enemy_shimo_ = (kami_seat_ + 3) % 4;
      dealer_first_seat_ = msg.value("dealer_first_seat", 0);
      return nullptr;
    }

    if (msg_type == "round_start") {
      int s = msg.value("target_seat", msg.value("seat", 0));
      kami_seat_ = (s / 2) * 2;
      shimo_seat_ = kami_seat_ + 1;
      enemy_kami_ = (kami_seat_ + 2) % 4;
      enemy_shimo_ = (kami_seat_ + 3) % 4;

      if (s == kami_seat_) {
        round_wind_ = msg.value("round_wind", "E");
        round_number_ = msg.value("round_number", 1);
        honba_ = msg.value("honba", 0);
        kyoutaku_ = msg.value("kyoutaku", 0);
        dealer_seat_ = msg.value("dealer_seat", 0);
        dora_indicators_ = msg.value("dora_indicators", std::vector<std::string>{});
        if (msg.contains("points")) {
          auto pts = msg["points"].get<std::vector<int>>();
          for (int i = 0; i < 4 && i < static_cast<int>(pts.size()); ++i) {
            points_[i] = pts[i];
          }
        }
        for (int i = 0; i < 4; ++i) {
          all_rivers_full_[i].clear();
          all_rivers_[i].clear();
          all_melds_[i].clear();
          opp_riichi_[i] = false;
          safe_after_riichi_[i].clear();
        }
        total_kans_ = 0;
        uncalled_first_round_ = true;
      }

      auto& st = seats_[s];
      st.seat_wind = msg.value("seat_wind", "E");
      st.hand = msg.value("hand", std::vector<std::string>{});
      st.melds.clear();
      st.is_riichi = false;
      st.is_double_riichi = false;
      st.is_ippatsu = false;
      st.temp_furiten = false;
      st.riichi_furiten = false;
      return nullptr;
    }

    if (msg_type == "dora_notify") {
      if (msg.contains("dora_indicators")) {
        dora_indicators_ = msg["dora_indicators"].get<std::vector<std::string>>();
      } else if (msg.contains("dora_indicator")) {
        dora_indicators_.push_back(msg["dora_indicator"].get<std::string>());
      }
      return nullptr;
    }

    if (msg_type == "discard_ack") {
      int s = msg.value("target_seat", msg.value("seat", kami_seat_));
      std::string tile = msg.value("tile", "");
      auto& st = seats_[s];
      remove_one_token(st.hand, tile);
      all_rivers_[s].push_back(tile);
      all_rivers_full_[s].push_back(tile);
      int k = token_to_kind34(tile);
      for (int i = 0; i < 4; ++i) {
        if (opp_riichi_[i]) safe_after_riichi_[i].insert(k);
      }
      st.temp_furiten = false;
      if (st.is_riichi) st.is_ippatsu = false;
      return nullptr;
    }

    if (msg_type == "riichi_accepted") {
      int s = msg.value("seat", -1);
      if (msg.contains("points")) {
        auto pts = msg["points"].get<std::vector<int>>();
        for (int i = 0; i < 4 && i < static_cast<int>(pts.size()); ++i) {
          points_[i] = pts[i];
        }
      } else if (s >= 0 && s < 4) {
        points_[s] -= 1000;
      }
      kyoutaku_ = msg.value("kyoutaku", kyoutaku_ + 1);
      if (s >= 0 && s < 4) {
        opp_riichi_[s] = true;
        if (is_our_seat(s)) {
          seats_[s].is_riichi = true;
          if (uncalled_first_round_ && all_rivers_full_[s].size() == 1) {
            seats_[s].is_double_riichi = true;
          }
          seats_[s].is_ippatsu = true;
        }
      }
      return nullptr;
    }

    if (msg_type == "call_notify") {
      uncalled_first_round_ = false;
      seats_[kami_seat_].is_ippatsu = false;
      seats_[shimo_seat_].is_ippatsu = false;
      int caller = msg.value("seat", -1);
      std::string mtype = msg.value("meld_type", "");
      std::vector<std::string> tiles = msg.value("tiles", std::vector<std::string>{});
      std::string called_tile =
          (msg.contains("called_tile") && !msg["called_tile"].is_null())
              ? msg["called_tile"].get<std::string>()
              : "";
      int from_seat =
          (msg.contains("from_seat") && !msg["from_seat"].is_null())
              ? msg["from_seat"].get<int>()
              : -1;

      if (mtype == "ankan" || mtype == "daiminkan" || mtype == "kakan") {
        total_kans_++;
      }
      if (from_seat >= 0 && from_seat < 4 && mtype != "ankan" && mtype != "kakan") {
        if (!all_rivers_[from_seat].empty() && all_rivers_[from_seat].back() == called_tile) {
          all_rivers_[from_seat].pop_back();
        }
      }
      if (caller >= 0 && caller < 4) {
        if (mtype == "kakan") {
          for (auto& m_tiles : all_melds_[caller]) {
            if (m_tiles.size() == 3 &&
                token_to_kind34(m_tiles[0]) == token_to_kind34(called_tile)) {
              m_tiles.push_back(called_tile);
              break;
            }
          }
        } else {
          all_melds_[caller].push_back(tiles);
        }
      }
      if (is_our_seat(caller)) {
        auto& st = seats_[caller];
        if (mtype == "kakan") {
          remove_one_token(st.hand, called_tile);
          for (auto& m : st.melds) {
            if (m.meld_type == "pon" &&
                token_to_kind34(m.tiles[0]) == token_to_kind34(called_tile)) {
              m.meld_type = "kakan";
              m.tiles.push_back(called_tile);
              break;
            }
          }
        } else {
          std::vector<std::string> to_remove = tiles;
          if (!called_tile.empty()) {
            remove_one_token(to_remove, called_tile);
          }
          for (const auto& t : to_remove) {
            remove_one_token(st.hand, t);
          }
          Meld m;
          m.meld_type = mtype;
          m.tiles = tiles;
          m.called_tile = called_tile;
          m.from_seat = from_seat;
          st.melds.push_back(m);
        }
      }
      return nullptr;
    }

    if (msg_type == "round_end") {
      if (msg.contains("scores_after")) {
        auto pts = msg["scores_after"].get<std::vector<int>>();
        for (int i = 0; i < 4 && i < static_cast<int>(pts.size()); ++i) {
          points_[i] = pts[i];
        }
      }
      return nullptr;
    }

    if (msg_type == "turn_tsumo") {
      int s = msg.value("target_seat", msg.value("seat", kami_seat_));
      auto& st = seats_[s];
      std::string drawn_tile = msg["tile"].get<std::string>();
      bool is_rinshan = msg.value("is_rinshan", false);
      int remaining_tiles = msg.value("remaining_tiles", 0);
      if (msg.contains("dora_indicators")) {
        dora_indicators_ = msg["dora_indicators"].get<std::vector<std::string>>();
      }
      if (msg.contains("hand")) {
        st.hand = msg["hand"].get<std::vector<std::string>>();
      }

      bool is_first_turn = (uncalled_first_round_ && all_rivers_full_[s].empty());
      bool is_haitei = (remaining_tiles == 0 && !is_rinshan);

      WinContext win_ctx;
      win_ctx.is_tsumo = true;
      win_ctx.winning_token = drawn_tile;
      win_ctx.seat_wind_kind34 = wind_to_kind34(st.seat_wind);
      win_ctx.round_wind_kind34 = wind_to_kind34(round_wind_);
      win_ctx.is_dealer = (s == dealer_seat_);
      win_ctx.is_riichi = st.is_riichi;
      win_ctx.is_double_riichi = st.is_double_riichi;
      win_ctx.is_ippatsu = st.is_ippatsu;
      win_ctx.is_rinshan = is_rinshan;
      win_ctx.is_haitei = is_haitei;
      win_ctx.is_first_uncalled_turn = is_first_turn;
      win_ctx.dora_indicators = dora_indicators_;
      win_ctx.rules = rules_;

      auto agari = evaluate_agari(st.hand, st.melds, win_ctx);
      if (agari.is_valid && should_take_win(s, agari, -1, remaining_tiles)) {
        st.hand.push_back(drawn_tile);
        return {{"seq", seq}, {"action", "tsumo"}};
      }

      std::vector<std::string> full_hand = st.hand;
      full_hand.push_back(drawn_tile);
      st.hand = full_hand;

      if (st.is_riichi) {
        return {{"seq", seq}, {"action", "discard"}, {"tile", drawn_tile}};
      }

      bool can_riichi = (st.melds.empty() && points_[s] >= 1000 && remaining_tiles >= 4);
      auto [discard_tile, declare_riichi] =
          choose_discard_for_seat(s, full_hand, {}, can_riichi, remaining_tiles);
      if (declare_riichi) {
        return {{"seq", seq}, {"action", "riichi"}, {"tile", discard_tile}};
      }
      return {{"seq", seq}, {"action", "discard"}, {"tile", discard_tile}};
    }

    if (msg_type == "turn_after_call") {
      int s = msg.value("target_seat", msg.value("seat", kami_seat_));
      auto& st = seats_[s];
      if (msg.contains("hand")) {
        st.hand = msg["hand"].get<std::vector<std::string>>();
      }
      std::set<int> forbidden_kinds;
      if (msg.contains("forbidden_discards")) {
        for (const auto& t : msg["forbidden_discards"].get<std::vector<std::string>>()) {
          forbidden_kinds.insert(token_to_kind34(t));
        }
      }
      auto [discard_tile, _] =
          choose_discard_for_seat(s, st.hand, forbidden_kinds, false, 20);
      return {{"seq", seq}, {"action", "discard"}, {"tile", discard_tile}};
    }

    if (msg_type == "discard_notify" || msg_type == "kakan_notify") {
      int s = msg.value("target_seat", kami_seat_);
      auto& st = seats_[s];
      std::string tile = msg["tile"].get<std::string>();
      int discarder = msg["seat"].get<int>();
      bool is_chankan = (msg_type == "kakan_notify");
      int remaining_tiles = msg.value("remaining_tiles", 1);
      int k = token_to_kind34(tile);

      if (!is_chankan && !is_our_seat(discarder) && s == kami_seat_) {
        all_rivers_[discarder].push_back(tile);
        all_rivers_full_[discarder].push_back(tile);
        for (int i = 0; i < 4; ++i) {
          if (opp_riichi_[i]) safe_after_riichi_[i].insert(k);
        }
      }

      bool is_houtei = (!is_chankan && remaining_tiles == 0);
      if (!is_furiten(s)) {
        WinContext win_ctx;
        win_ctx.is_tsumo = false;
        win_ctx.winning_token = tile;
        win_ctx.seat_wind_kind34 = wind_to_kind34(st.seat_wind);
        win_ctx.round_wind_kind34 = wind_to_kind34(round_wind_);
        win_ctx.is_dealer = (s == dealer_seat_);
        win_ctx.is_riichi = st.is_riichi;
        win_ctx.is_double_riichi = st.is_double_riichi;
        win_ctx.is_ippatsu = st.is_ippatsu;
        win_ctx.is_chankan = is_chankan;
        win_ctx.is_houtei = is_houtei;
        win_ctx.dora_indicators = dora_indicators_;
        win_ctx.rules = rules_;

        auto agari = evaluate_agari(st.hand, st.melds, win_ctx);
        if (agari.is_valid && should_take_win(s, agari, discarder, remaining_tiles)) {
          return {{"seq", seq}, {"action", "ron"}};
        }
      }

      auto waits = get_tenpai_waits(st.hand, st.melds);
      if (waits.count(k)) {
        st.temp_furiten = true;
        if (st.is_riichi) st.riichi_furiten = true;
      }

      if (!is_chankan) {
        json call_action = consider_call_for_seat(s, tile, discarder, remaining_tiles);
        if (!call_action.is_null()) {
          call_action["seq"] = seq;
          return call_action;
        }
      }

      return {{"seq", seq}, {"action", "pass"}};
    }

    return nullptr;
  }
};

}  // namespace mahjong

int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  mahjong::TagPM0Client client;
  client.run();
  return 0;
}
