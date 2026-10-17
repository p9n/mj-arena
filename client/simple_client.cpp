// C++20 port of client/simple_client.py
// Compile: g++ -std=c++20 -O2 client/simple_client.cpp -o client/simple_client

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <iostream>
#include <map>
#include <nlohmann/json.hpp>
#include <set>
#include <string>
#include <thread>
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
  std::string ranking_mode = "normal";

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
  std::string wait_type;  // "ryanmen", "kanchan", "penchan", "shabo", "tanki", "kokushi13"
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

// ---------------- SimpleClient ----------------

class SimpleClient {
 public:
  explicit SimpleClient(std::string mode = "normal") : mode_(std::move(mode)) {}

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
  std::string mode_ = "normal";
  int seat_ = 0;
  int dealer_seat_ = 0;
  std::string round_wind_ = "E";
  std::string seat_wind_ = "E";
  std::vector<std::string> hand_;
  std::vector<Meld> melds_;
  std::vector<std::string> river_;
  std::array<std::vector<std::string>, 4> all_rivers_;
  std::array<std::vector<std::vector<std::string>>, 4> all_melds_;
  int total_kans_ = 0;
  bool is_riichi_ = false;
  bool is_double_riichi_ = false;
  bool is_ippatsu_ = false;
  bool temp_furiten_ = false;
  bool riichi_furiten_ = false;
  std::vector<std::string> dora_indicators_;
  std::array<int, 4> points_ = {25000, 25000, 25000, 25000};
  RuleConfig rules_;
  bool triggered_test_action_ = false;

  std::set<int> yakuhai_kinds() const {
    std::set<int> res = DRAGON_KINDS;
    res.insert(wind_to_kind34(round_wind_));
    res.insert(wind_to_kind34(seat_wind_));
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
      const std::vector<std::string>& closed_hand) const {
    std::array<int, 34> counts{};
    for (const auto& t : closed_hand) counts[token_to_kind34(t)] += 1;
    for (const auto& r : all_rivers_) {
      for (const auto& t : r) counts[token_to_kind34(t)] += 1;
    }
    for (const auto& m_list : all_melds_) {
      for (const auto& m_tiles : m_list) {
        for (const auto& t : m_tiles) counts[token_to_kind34(t)] += 1;
      }
    }
    for (const auto& ind : dora_indicators_) counts[token_to_kind34(ind)] += 1;
    return counts;
  }

  bool is_furiten(const std::vector<std::string>* hand13 = nullptr) const {
    if (temp_furiten_ || riichi_furiten_) return true;
    const auto& target_hand = hand13 ? *hand13 : hand_;
    auto waits = get_tenpai_waits(target_hand, melds_);
    if (waits.empty()) return false;
    for (const auto& t : river_) {
      if (waits.count(token_to_kind34(t))) return true;
    }
    return false;
  }

  double tile_keep_value(const std::string& token,
                         const std::array<int, 34>& counts34) const {
    int k = token_to_kind34(token);
    double val = 0.0;
    if (is_aka_token(token)) val += 3.5;
    for (int dk : dora_kinds()) {
      if (k == dk) val += 3.0;
    }
    auto yh = yakuhai_kinds();
    if (yh.count(k)) {
      val += (counts34[k] >= 2) ? 4.5 : 1.2;
    } else if (k >= 27) {
      val += (counts34[k] == 1) ? 0.2 : 1.5;
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

  std::tuple<int, double, bool> score_hand13(
      const std::vector<std::string>& hand13, const std::vector<Meld>& melds,
      const std::array<int, 34>& vis_counts, bool can_riichi) const {
    auto counts34 = tokens_to_counts34(hand13);
    int num_melds = static_cast<int>(melds.size());
    int shanten = calculate_shanten(counts34, num_melds);

    if (shanten == 0) {
      auto waits = get_tenpai_waits(hand13, melds);
      if (waits.empty()) return {1, 0.0, false};

      bool is_furiten_wait = temp_furiten_ || riichi_furiten_;
      for (const auto& t : river_) {
        if (waits.count(token_to_kind34(t))) is_furiten_wait = true;
      }

      int total_wait_copies = 0;
      double yaku_wait_copies = 0.0;
      int riichi_wait_copies = 0;
      for (int w : waits) {
        int rem_copies = std::max(0, 4 - vis_counts[w]);
        total_wait_copies += rem_copies;
        std::string w_tok = kind34_to_token(w);

        WinContext ctx_dama;
        ctx_dama.is_tsumo = false;
        ctx_dama.winning_token = w_tok;
        ctx_dama.seat_wind_kind34 = wind_to_kind34(seat_wind_);
        ctx_dama.round_wind_kind34 = wind_to_kind34(round_wind_);
        ctx_dama.is_dealer = (seat_ == dealer_seat_);
        ctx_dama.is_riichi = false;
        ctx_dama.dora_indicators = dora_indicators_;
        ctx_dama.rules = rules_;

        if (evaluate_agari(hand13, melds, ctx_dama).is_valid) {
          yaku_wait_copies += rem_copies;
        } else if (can_riichi) {
          riichi_wait_copies += rem_copies;
        } else {
          WinContext ctx_tsumo = ctx_dama;
          ctx_tsumo.is_tsumo = true;
          if (evaluate_agari(hand13, melds, ctx_tsumo).is_valid) {
            yaku_wait_copies += rem_copies * 0.4;
          }
        }
      }

      double effective_copies =
          yaku_wait_copies + (can_riichi ? riichi_wait_copies : 0);
      double furiten_mult = is_furiten_wait ? 0.35 : 1.0;
      double keep_bonus = 0.0;
      for (const auto& t : hand13) keep_bonus += tile_keep_value(t, counts34);
      keep_bonus *= 0.01;

      double eff_score =
          (effective_copies * 100.0 + total_wait_copies * 10.0) * furiten_mult +
          keep_bonus;
      bool should_riichi =
          can_riichi && total_wait_copies > 0 && !is_furiten_wait;
      if (can_riichi && total_wait_copies > 0 && yaku_wait_copies == 0.0) {
        should_riichi = true;
      }
      return {0, eff_score, should_riichi};
    }

    int ukeire_copies = 0;
    for (int k = 0; k < 34; ++k) {
      if (counts34[k] >= 4) continue;
      int rem = 4 - vis_counts[k];
      if (rem <= 0) continue;
      counts34[k] += 1;
      if (calculate_shanten(counts34, num_melds) < shanten) {
        ukeire_copies += rem;
      }
      counts34[k] -= 1;
    }

    double keep_bonus = 0.0;
    for (const auto& t : hand13) keep_bonus += tile_keep_value(t, counts34);
    keep_bonus *= 0.05;
    return {shanten, ukeire_copies * 10.0 + keep_bonus, false};
  }

  std::pair<std::string, std::string> choose_discard(
      const std::vector<std::string>& hand14, const std::string* drawn_tile,
      const std::set<std::string>& forbidden, int remaining_tiles = 10) const {
    if (is_riichi_ && drawn_tile != nullptr) {
      return {"discard", *drawn_tile};
    }
    if ((mode_ == "tsumogiri" || mode_ == "timeout") &&
        drawn_tile != nullptr && !forbidden.count(*drawn_tile)) {
      return {"discard", *drawn_tile};
    }

    bool is_menzen = true;
    for (const auto& m : melds_) {
      if (m.is_open()) is_menzen = false;
    }
    bool can_riichi = !is_riichi_ && is_menzen && points_[seat_] >= 1000 &&
                      drawn_tile != nullptr && remaining_tiles >= 4;

    auto vis_counts = visible_counts(hand14);
    int best_shanten = 99;
    double best_score = -1e9;
    std::string best_tile = hand14.back();
    bool best_riichi = false;
    std::set<std::string> seen_candidates;
    auto dks = dora_kinds();

    for (size_t i = 0; i < hand14.size(); ++i) {
      const auto& candidate = hand14[i];
      if (forbidden.count(candidate) || seen_candidates.count(candidate)) {
        continue;
      }
      seen_candidates.insert(candidate);
      std::vector<std::string> rem13;
      rem13.reserve(hand14.size() - 1);
      for (size_t j = 0; j < hand14.size(); ++j) {
        if (j != i) rem13.push_back(hand14[j]);
      }

      auto [shanten, eff_score, should_riichi] =
          score_hand13(rem13, melds_, vis_counts, can_riichi);

      double discard_penalty = 0.0;
      if (is_aka_token(candidate)) discard_penalty += 0.25;
      int ck = token_to_kind34(candidate);
      if (std::find(dks.begin(), dks.end(), ck) != dks.end()) {
        discard_penalty += 0.2;
      }
      eff_score -= discard_penalty;

      if (shanten < best_shanten ||
          (shanten == best_shanten && eff_score > best_score)) {
        best_shanten = shanten;
        best_score = eff_score;
        best_tile = candidate;
        best_riichi = should_riichi;
      }
    }

    if (best_riichi && can_riichi) return {"riichi", best_tile};
    return {"discard", best_tile};
  }

  bool has_open_yaku_plan(const std::vector<std::string>& rem_hand_after_discard,
                          const std::vector<Meld>& new_melds) const {
    auto yakuhai = yakuhai_kinds();
    for (const auto& m : new_melds) {
      if (m.meld_type == "pon" || m.meld_type == "daiminkan" ||
          m.meld_type == "ankan" || m.meld_type == "kakan") {
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

  static void remove_one(std::vector<std::string>& vec,
                         const std::string& val) {
    auto it = std::find(vec.begin(), vec.end(), val);
    if (it != vec.end()) vec.erase(it);
  }

  json consider_call(const std::string& discarded_tile, int discarder_seat,
                     int remaining_tiles) const {
    if (is_riichi_ || remaining_tiles <= 0 || mode_ != "normal") {
      return nullptr;
    }

    auto vis_counts = visible_counts(hand_);
    int shanten_before = calculate_shanten(tokens_to_counts34(hand_),
                                           static_cast<int>(melds_.size()));
    int k_disc = token_to_kind34(discarded_tile);
    auto yakuhai = yakuhai_kinds();

    json best_call = nullptr;
    int best_shanten = shanten_before;
    double best_score = -1e9;

    // Candidate 1: Pon
    std::vector<std::string> matching;
    for (const auto& t : hand_) {
      if (token_to_kind34(t) == k_disc) matching.push_back(t);
    }
    if (matching.size() >= 2) {
      std::stable_sort(matching.begin(), matching.end(),
                       [](const std::string& a, const std::string& b) {
                         return (is_aka_token(a) ? 0 : 1) <
                                (is_aka_token(b) ? 0 : 1);
                       });
      std::vector<std::string> pon_tiles = {matching[0], matching[1]};
      std::vector<std::string> rem_hand = hand_;
      for (const auto& pt : pon_tiles) remove_one(rem_hand, pt);

      auto forb_kinds =
          get_kuikae_forbidden_kinds("pon", discarded_tile, pon_tiles);
      std::set<std::string> legal_discards;
      for (const auto& t : rem_hand) {
        if (!forb_kinds.count(token_to_kind34(t))) legal_discards.insert(t);
      }
      if (!legal_discards.empty()) {
        std::vector<Meld> new_melds = melds_;
        new_melds.push_back(
            Meld{"pon",
                 {pon_tiles[0], pon_tiles[1], discarded_tile},
                 discarded_tile,
                 discarder_seat});
        for (const auto& d : legal_discards) {
          std::vector<std::string> rem_after = rem_hand;
          remove_one(rem_after, d);
          if (!has_open_yaku_plan(rem_after, new_melds)) continue;
          auto [sh_after, eff_after, _] =
              score_hand13(rem_after, new_melds, vis_counts, false);
          bool allow_equal = (yakuhai.count(k_disc) && shanten_before <= 2);
          if (sh_after < best_shanten ||
              (allow_equal && sh_after == shanten_before &&
               best_call.is_null()) ||
              (sh_after == best_shanten && !best_call.is_null() &&
               eff_after > best_score)) {
            best_shanten = sh_after;
            best_score = eff_after;
            best_call = {{"action", "pon"}, {"tiles", pon_tiles}};
          }
        }
      }
    }

    // Candidate 2: Chi (only from Kamicha)
    if ((discarder_seat + 1) % 4 == seat_ && k_disc < 27) {
      int suit_base = (k_disc / 9) * 9;
      int num = k_disc - suit_base;
      std::vector<std::pair<int, int>> chi_patterns;
      if (num >= 2) chi_patterns.push_back({suit_base + num - 2, suit_base + num - 1});
      if (1 <= num && num <= 7) chi_patterns.push_back({suit_base + num - 1, suit_base + num + 1});
      if (num <= 6) chi_patterns.push_back({suit_base + num + 1, suit_base + num + 2});

      for (const auto& [k1, k2] : chi_patterns) {
        std::vector<std::string> t1_list, t2_list;
        for (const auto& t : hand_) {
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
        std::vector<std::string> rem_hand = hand_;
        for (const auto& ct : chi_tiles) remove_one(rem_hand, ct);

        auto forb_kinds =
            get_kuikae_forbidden_kinds("chi", discarded_tile, chi_tiles);
        std::set<std::string> legal_discards;
        for (const auto& t : rem_hand) {
          if (!forb_kinds.count(token_to_kind34(t))) legal_discards.insert(t);
        }
        if (legal_discards.empty()) continue;

        std::vector<Meld> new_melds = melds_;
        new_melds.push_back(
            Meld{"chi",
                 {chi_tiles[0], chi_tiles[1], discarded_tile},
                 discarded_tile,
                 discarder_seat});
        for (const auto& d : legal_discards) {
          std::vector<std::string> rem_after = rem_hand;
          remove_one(rem_after, d);
          if (!has_open_yaku_plan(rem_after, new_melds)) continue;
          auto [sh_after, eff_after, _] =
              score_hand13(rem_after, new_melds, vis_counts, false);
          if (sh_after < shanten_before && sh_after <= 1) {
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

  json handle_message(const json& msg) {
    std::string msg_type = msg.value("type", "");
    int seq = msg.value("seq", 0);

    if (msg_type == "match_start") {
      seat_ = msg["seat"].get<int>();
      if (msg.contains("rules")) {
        rules_ = RuleConfig::from_json(msg["rules"]);
      }
      return nullptr;
    }

    if (msg_type == "round_start") {
      seat_ = msg["seat"].get<int>();
      dealer_seat_ = msg["dealer_seat"].get<int>();
      round_wind_ = msg["round_wind"].get<std::string>();
      seat_wind_ = msg["seat_wind"].get<std::string>();
      hand_ = msg["hand"].get<std::vector<std::string>>();
      melds_.clear();
      river_.clear();
      for (auto& r : all_rivers_) r.clear();
      for (auto& m : all_melds_) m.clear();
      total_kans_ = 0;
      is_riichi_ = false;
      is_double_riichi_ = false;
      is_ippatsu_ = false;
      temp_furiten_ = false;
      riichi_furiten_ = false;
      dora_indicators_ =
          msg.value("dora_indicators", std::vector<std::string>{});
      if (msg.contains("points")) {
        auto pts = msg["points"].get<std::vector<int>>();
        for (int i = 0; i < 4 && i < static_cast<int>(pts.size()); ++i) {
          points_[i] = pts[i];
        }
      } else {
        points_ = {25000, 25000, 25000, 25000};
      }
      return nullptr;
    }

    if (msg_type == "dora_notify") {
      if (msg.contains("dora_indicators")) {
        dora_indicators_ =
            msg["dora_indicators"].get<std::vector<std::string>>();
      }
      return nullptr;
    }

    if (msg_type == "riichi_accepted") {
      int r_seat = msg["seat"].get<int>();
      if (msg.contains("points")) {
        auto pts = msg["points"].get<std::vector<int>>();
        for (int i = 0; i < 4 && i < static_cast<int>(pts.size()); ++i) {
          points_[i] = pts[i];
        }
      }
      if (r_seat == seat_) {
        is_riichi_ = true;
        is_double_riichi_ = msg.value("is_double_riichi", false);
        is_ippatsu_ = true;
      }
      return nullptr;
    }

    if (msg_type == "call_notify") {
      int c_seat = msg["seat"].get<int>();
      std::string m_type = msg["meld_type"].get<std::string>();
      auto m_tiles = msg.value("tiles", std::vector<std::string>{});
      int from_seat = -1;
      if (msg.contains("from_seat") && !msg["from_seat"].is_null()) {
        from_seat = msg["from_seat"].get<int>();
      }
      if (m_type == "daiminkan" || m_type == "ankan" || m_type == "kakan") {
        total_kans_ += 1;
      }
      if (from_seat >= 0 &&
          (m_type == "chi" || m_type == "pon" || m_type == "daiminkan")) {
        if (!all_rivers_[from_seat].empty()) {
          all_rivers_[from_seat].pop_back();
        }
      }
      all_melds_[c_seat].push_back(m_tiles);

      if (c_seat == seat_) {
        std::string called_tile;
        if (msg.contains("called_tile") && !msg["called_tile"].is_null()) {
          called_tile = msg["called_tile"].get<std::string>();
        }
        if (m_type == "chi" || m_type == "pon" || m_type == "daiminkan" ||
            m_type == "ankan") {
          melds_.push_back(Meld{m_type, m_tiles, called_tile, from_seat});
        } else if (m_type == "kakan") {
          for (auto& m : melds_) {
            if (m.meld_type == "pon" &&
                token_to_kind34(m.tiles[0]) == token_to_kind34(called_tile)) {
              m = Meld{"kakan", m_tiles, called_tile, m.from_seat};
              break;
            }
          }
        }
      }
      is_ippatsu_ = false;
      return nullptr;
    }

    if (msg_type == "discard_ack") {
      std::string tile = msg["tile"].get<std::string>();
      remove_one(hand_, tile);
      river_.push_back(tile);
      all_rivers_[seat_].push_back(tile);
      temp_furiten_ = false;
      if (is_riichi_) is_ippatsu_ = false;
      return nullptr;
    }

    if (msg_type == "turn_tsumo") {
      std::string drawn_tile = msg["tile"].get<std::string>();
      if (msg.contains("hand")) {
        hand_ = msg["hand"].get<std::vector<std::string>>();
      }
      int remaining_tiles = msg.value("remaining_tiles", 10);
      bool is_rinshan = msg.value("is_rinshan", false);

      if (mode_ == "mem-hog" && !triggered_test_action_) {
        triggered_test_action_ = true;
        std::vector<char> hog(512ULL * 1024ULL * 1024ULL, 1);
        (void)hog;
      }
      if (mode_ == "timeout" && !triggered_test_action_) {
        triggered_test_action_ = true;
        hand_.push_back(drawn_tile);
        std::this_thread::sleep_for(std::chrono::milliseconds(6000));
        return {{"seq", seq}, {"action", "discard"}, {"tile", drawn_tile}};
      }
      if (mode_ == "chombo" && !triggered_test_action_) {
        triggered_test_action_ = true;
        hand_.push_back(drawn_tile);
        return {{"seq", seq}, {"action", "discard"}, {"tile", "9z"}};
      }

      // 1. Check Tsumo win
      WinContext win_ctx;
      win_ctx.is_tsumo = true;
      win_ctx.winning_token = drawn_tile;
      win_ctx.seat_wind_kind34 = wind_to_kind34(seat_wind_);
      win_ctx.round_wind_kind34 = wind_to_kind34(round_wind_);
      win_ctx.is_dealer = (seat_ == dealer_seat_);
      win_ctx.is_riichi = is_riichi_;
      win_ctx.is_double_riichi = is_double_riichi_;
      win_ctx.is_ippatsu = is_ippatsu_;
      win_ctx.is_rinshan = is_rinshan;
      win_ctx.is_haitei = (remaining_tiles == 0 && !is_rinshan);
      win_ctx.dora_indicators = dora_indicators_;
      win_ctx.rules = rules_;

      if (evaluate_agari(hand_, melds_, win_ctx).is_valid) {
        return {{"seq", seq}, {"action", "tsumo"}};
      }

      std::vector<std::string> hand14 = hand_;
      hand14.push_back(drawn_tile);

      // 2. Check Riichi Ankan if in Riichi
      if (is_riichi_ && mode_ == "normal" && remaining_tiles > 0 &&
          total_kans_ < 4) {
        if (can_ankan_in_riichi(hand_, drawn_tile, melds_, drawn_tile)) {
          hand_ = hand14;
          return {{"seq", seq}, {"action", "ankan"}, {"tile", drawn_tile}};
        }
      }

      // 3. Choose optimal discard (or Riichi)
      auto [action, disc_tile] =
          choose_discard(hand14, &drawn_tile, {}, remaining_tiles);
      if (action == "riichi" && remaining_tiles < 4) {
        action = "discard";
      }
      hand_ = hand14;
      return {{"seq", seq}, {"action", action}, {"tile", disc_tile}};
    }

    if (msg_type == "turn_after_call") {
      if (msg.contains("hand")) {
        hand_ = msg["hand"].get<std::vector<std::string>>();
      }
      std::set<std::string> forbidden;
      if (msg.contains("forbidden_discards")) {
        for (const auto& t :
             msg["forbidden_discards"].get<std::vector<std::string>>()) {
          forbidden.insert(t);
        }
      }
      auto [_, disc_tile] = choose_discard(hand_, nullptr, forbidden);
      return {{"seq", seq}, {"action", "discard"}, {"tile", disc_tile}};
    }

    if (msg_type == "discard_notify" || msg_type == "kakan_notify") {
      std::string tile = msg["tile"].get<std::string>();
      int discarder = msg["seat"].get<int>();
      bool is_chankan = (msg_type == "kakan_notify");
      int remaining_tiles = msg.value("remaining_tiles", 1);
      if (!is_chankan) {
        all_rivers_[discarder].push_back(tile);
      }
      bool is_houtei = (!is_chankan && remaining_tiles == 0);

      if (mode_ != "tsumogiri" && !is_furiten()) {
        WinContext win_ctx;
        win_ctx.is_tsumo = false;
        win_ctx.winning_token = tile;
        win_ctx.seat_wind_kind34 = wind_to_kind34(seat_wind_);
        win_ctx.round_wind_kind34 = wind_to_kind34(round_wind_);
        win_ctx.is_dealer = (seat_ == dealer_seat_);
        win_ctx.is_riichi = is_riichi_;
        win_ctx.is_double_riichi = is_double_riichi_;
        win_ctx.is_ippatsu = is_ippatsu_;
        win_ctx.is_chankan = is_chankan;
        win_ctx.is_houtei = is_houtei;
        win_ctx.dora_indicators = dora_indicators_;
        win_ctx.rules = rules_;

        if (evaluate_agari(hand_, melds_, win_ctx).is_valid) {
          return {{"seq", seq}, {"action", "ron"}};
        }
      }

      auto waits = get_tenpai_waits(hand_, melds_);
      if (waits.count(token_to_kind34(tile))) {
        temp_furiten_ = true;
        if (is_riichi_) riichi_furiten_ = true;
      }

      if (!is_chankan && mode_ == "normal") {
        json call_action = consider_call(tile, discarder, remaining_tiles);
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

int main(int argc, char** argv) {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);

  std::string mode = "normal";
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      mode = argv[++i];
    } else if (arg.rfind("--mode=", 0) == 0) {
      mode = arg.substr(7);
    }
  }

  mahjong::SimpleClient client(mode);
  client.run();
  return 0;
}
