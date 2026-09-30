"""Build a corpus: the truth layer (manifest.jsonl) and, with --render, the files (files.jsonl).

生成语料：真值层（manifest.jsonl），加 `--render` 渲染成文件（files.jsonl）。

    mirobody-gen build --seed 7 --out out/p3
    mirobody-gen build --seed 7 --people 8 --out out/smoke --stats
    mirobody-gen build --seed 7 --out out/p3 --render --pairs 12

同 seed 必然产出同一批人、同一条时间线、同一组数值。**这是可重放的全部含义**：
`out/` 随时可以删，判据是"生成器 + seed"，不是那堆字节。

生成完请立刻跑两道闸门——它们不是可选的收尾动作，是这套语料能不能用的判据：

    mirobody-gen audit-clinical out/p3/manifest.jsonl
    mirobody-gen audit-privacy --targets mirobody_gen/resources out/p3
"""

from __future__ import annotations

import argparse
import collections
import pathlib

from . import spec, manifest, person as person_mod



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--people", type=int, default=None, help="generate only the first N people (smoke builds)")
    ap.add_argument("--out", default="out/p1")
    ap.add_argument("--stats", action="store_true", help="print distribution statistics for comparison with the reference aggregates")
    ap.add_argument("--render", action="store_true", help="render files (text-layer PDF, XLSX/CSV, scans, photos, screenshots) and write files.jsonl")
    ap.add_argument("--pairs", type=int, default=0, help="also emit N minimal-contrast pair groups (pairs.jsonl)")
    ap.add_argument("--paraphrase", action="store_true", help="use resources/paraphrases.json for narrative wording (off by default)")
    ap.add_argument("--no-banner", action="store_true",
                    help="omit the SYNTHETIC banner (realism stress test; the synthetic metadata mark is always kept)")
    args = ap.parse_args()
    spec.set_paraphrases(bool(args.paraphrase))

    people = person_mod.build_cohort(args.seed, args.people)
    encounters = {p.person_id: person_mod.encounters_for(p, args.seed) for p in people}

    out_dir = pathlib.Path(args.out)
    n_enc, n_rows = manifest.write(out_dir, people, encounters)
    print(f"{len(people)} 人 · {n_enc} 次就诊 · {n_rows} 行读数 → {out_dir}")

    # 同一个人的另外三条来源——设备批次（POST /api/data）、日记句子、基因导出文件。
    from . import devices, genomics, journal

    langs = {p.person_id: person_mod.person_lang(args.seed, p.person_id) for p in people}
    n_dev = devices.write_all(out_dir, people, args.seed, langs)
    n_journal = journal.write_all(out_dir, people, args.seed, langs)
    n_gen = genomics.write_all(out_dir, people, args.seed, langs)
    print(f"设备记录 {n_dev} 条（devices.jsonl）· 日记 {n_journal} 条（journal.jsonl）· 基因文件 {n_gen} 份（genomics.jsonl）")

    if args.stats:
        _stats(people, encounters)

    if args.render or args.pairs:
        from . import corpus, pairs

        if args.render:
            records = corpus.render_corpus(args.seed, people, encounters, out_dir, banner=not args.no_banner)
            by_fmt = collections.Counter(r["format"] for r in records)
            print(f"渲染 {len(records)} 份文件 {dict(by_fmt)} → {out_dir / 'files'}")
        if args.pairs:
            records = pairs.render_pairs(args.seed, people, encounters, out_dir, args.pairs,
                                         banner=not args.no_banner)
            print(f"最小对照对 {args.pairs} 组，{len(records)} 份文件 → {out_dir / 'pairs'}")


def _stats(people, encounters) -> None:
    from . import spec

    rows_per = [len(e.readings) for items in encounters.values() for e in items]
    rows_per.sort()

    def p(q: float) -> int:
        return rows_per[min(int(len(rows_per) * q), len(rows_per) - 1)]

    print(f"\n每次就诊的行数  p25={p(.25)} p50={p(.5)} p75={p(.75)} p95={p(.95)} "
          f"max={rows_per[-1]}")
    print("（实测真实语料的每文档行数：p25=1 p50=6 p75=15 p95=36 max=86。"
          "一次就诊会拆成多份文件，所以这里偏大是正常的，拆成文件后再对）")

    per_person = collections.Counter(len(v) for v in encounters.values())
    print(f"每人就诊次数  {dict(sorted(per_person.items()))}")

    # 异常率：这是能与真实语料直接对照的量，也是"队列按患病率配比"是否奏效的检验。
    abnormal: dict[str, list[int]] = collections.defaultdict(list)
    for items in encounters.values():
        for encounter in items:
            for reading in encounter.readings:
                abnormal[reading.key].append(reading.status != "normal")
    print("\n异常率（生成 vs 真实语料实测）：")
    observed = {"chol": 0.274, "tg": 0.31, "ldl": 0.205, "ua": 0.143, "hgb": 0.009,
                "glu": 0.026, "alt": 0.021, "crea": 0.037, "hba1c": 0.03, "plt": 0.0}
    for key, truth in observed.items():
        flags = abnormal.get(key) or []
        if flags:
            print(f"  {spec.indicators()[key]['zh']:<12} 生成 {sum(flags)/len(flags):>6.1%}"
                  f"   真实 {truth:>6.1%}   n={len(flags)}")

    print("\n注：真实语料那一列来自**住院与门诊混合**的人群，异常率天然偏高；"
          "我们的队列是按人群患病率配的。两者不该一致，但数量级差太远就说明配比错了。")


if __name__ == "__main__":
    main()
