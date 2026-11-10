# サンガー配列ソフトの使い分け

[English](COMPARISON.md) · [namiyomiの説明へ](../README-ja.md)

namiyomiは、AB1の波形を確認し、同一個体・同一領域のリードからコンティグを作り、編集した配列をFASTAで取り出す用途に絞っています。最大の特徴は、**機能を必要最小限に絞っている**ことです。AB1からFASTAまでの作業だけを設定の少ない1つの画面にまとめているため、使い始める前に覚えることがほとんどありません。さらに、**無償**で使えること（ライセンス料や更新料がかかりません）、ファイル名の接頭辞による**自動コンティグ作成（AUTO）**と選択リードからの手動作成の両方を備えること、元リードとコンセンサスを同時に編集・確認できることも強みです。

以下は、**2026年11月4日**に開発元の公式説明とリポジトリを確認してまとめた比較です。Sequencher、CodonCode Aligner、GENETYX / ATGCは**2026年11月10日**に追加しました。速度、精度、メモリ使用量、使いやすさを測定したランキングではありません。料金体系と対応OSは変更される場合があります。

## デスクトップアプリ

| ソフト | 波形確認・コンティグ作成 | 自動処理・検索 | 配布・料金体系 |
| --- | --- | --- | --- |
| **namiyomi 0.1.0** | 2本以上を選択して作成。コンセンサスの下に元リードの波形を表示し、元リードの編集からコンセンサスを再計算。AB1へ戻して作り直す操作も用意。 | 未使用リードを、元のファイル名の最初の`_`より前でまとめるAUTO。検索は両鎖、80～100%、ギャップなしの文字一致。 | MITで無償。Apple Silicon/macOS 15以降、Windows 10/11 x64試験版。Windows実機での一連の操作確認は未完了。Macは未公証。 |
| **[ChromasPro](https://technelysium.com.au/wp/chromaspro/)** | グラフィカルな重複アセンブリ、コンセンサス、曖昧な位置の表示、品質トリミング、整列した波形の編集。[更新履歴](https://technelysium.com.au/wp/chromaspro-version-history/)には、選択配列だけのアセンブリも記載。 | 完全一致・最適アラインメント検索と、コンセンサス検索に対応。namiyomiと同じ接頭辞ルールのAUTOは、参照した公開説明では確認できませんでした。「機能がない」という意味ではありません。 | 公式案内はWindows。有償ですが、[月額と買い切りの両方](https://technelysium.com.au/wp/purchase/)があり、試用版も提供。 |
| **[CodonCode Aligner](https://codoncode.com/aligner/features.htm)** | 複数のアルゴリズムによるアセンブリ、品質に基づくコンセンサス、波形・コンティグの編集、低品質末端やベクターの除去、ヘテロ接合のSNP・挿入欠失の検出。 | [Assemble by Name](https://codoncode.com/aligner/learn/sequence-assembly/how-to-assemble-by-name.htm)で、名前の一部により多数のサンプルを一括でグループ化。クローニングやプライマー設計の機能もあり。 | 有償。[買い切りライセンス](https://codoncode.com/aligner/aligner_prices.htm)（アカデミック・企業、単一ユーザー・同時利用）と任意の年間保守、学生・授業用ライセンス、30日間の試用版。[Windows 7～11（64-bit）、macOS 10.12以降](https://codoncode.com/aligner/download.htm)。 |
| **[Geneious Prime](https://www.geneious.com/tutorials/assembling-chromatograms)** | Sangerのトリミング、de novo・参照配列へのアセンブリ、波形・コンセンサス編集を含む統合環境。 | [Assemble by Name](https://manual.geneious.com/en/latest/AssemblyMapping.html)は、区切り文字と名前の何番目を使うかを指定してグループ化。[ワークフロー](https://www.geneious.com/tutorials/automated-workflows)で、トリミングから下流解析まで連結可能。 | Windows/macOS/Linux。[現行の公開プラン](https://www.geneious.com/pricing)は年額制。試用版あり。旧バージョンには買い切りライセンスも存在。 |
| **[GENETYX](https://www.genetyx.co.jp/genetyx_series/genetyx-ver-18/) / [ATGC](https://www.genetyx.co.jp/genetyx_serie/atgc-win-ver-12/)** | 国産の遺伝情報処理ソフト。アセンブラーのATGC（単体販売もあり）は、大量のフラグメントを高速に結合し、波形の信頼度を考慮してコンセンサスを決定。配列と波形の同時表示、読み込み時のベクター配列除去、NGSのマッピング・de novoアセンブリにも対応。 | namiyomiのAUTOに相当するファイル名による一括グループ化は、参照した製品ページでは確認できませんでした。「機能がない」という意味ではありません。 | 有償。[買い切りで年間利用料なし](https://www.genetyx.co.jp/prices/)。スタンドアロン・ネットワーク・教育用ライセンスと任意の年間保守。ATGC Ver.12はWindows 11、[ATGC-MAC Ver.10](https://www.genetyx.co.jp/genetyx_series/atgc-mac-ver-10/)はmacOS Ventura以降（Apple SiliconではRosetta 2が必要）。インターネットでのライセンス認証が必要。 |
| **[SeqTrace](https://github.com/stuckyb/seqtrace)** | F/Rの波形を確認・編集し、ペアのコンセンサスを作る用途に絞ったGUI。トリミングとFASTA出力にも対応。 | READMEはF/Rの自動識別、アラインメント、プロジェクト全体のコンセンサス処理を案内。 | 無償、ソースの表記は[GPL-3.0以降](https://github.com/stuckyb/seqtrace/blob/master/src/seqtrace/__init__.py)。Linux/Windows/macOSを案内していますが、[導入説明](https://github.com/stuckyb/seqtrace/wiki/Installation)はPython 2/PyGTK時代の内容を含みます。今回、現行OSでの互換性は検証していません。 |
| **[Sequencher](https://genecodes.com/sequencher-features/sanger-sequencing)** | 参照配列へのアセンブリを含むSangerのアセンブリ、保存できる条件でのトリミング、曖昧な塩基やヘテロ接合の確認、SNP検出。NGS機能もあり。 | [Assemble by Name](https://genecodes.com/sequencher-features/automated-analysis)で、名前の指定部分（正規表現も可）によりフラグメントをまとめ、コンティグ名も自動で付与。 | 有償。[買い切りライセンス](https://genecodes.com/support/faq)、6か月・12か月のプロジェクトライセンス、任意の年間保守、15日間の評価版。Windows/Mac。FAQには、ver 5.4.6は64-bitのmacOSに非対応で、64-bit Mac版はベータ版と記載されています。購入前に最新の対応状況を確認してください。 |
| **[UGENE](https://ugene.net/)** | [Sanger Reads Editor](https://ugene.net/docs/sanger-reads-editor/)と[CAP3連携](https://ugene.net/docs/extensions/cap3/)を含む統合解析環境。 | 解析ツールとワークフローを幅広く提供。CAP3アセンブリと波形エディタは別の構成要素として案内されているため、すべての結果が同じ波形画面へ開くとは限りません。 | 無償・オープンソース。Windows/macOS/Linux。リポジトリのライセンスは[GPL-2.0](https://github.com/ugeneunipro/ugene/blob/master/LICENSE.txt)。 |

Geneiousについては、[旧Prime 2019の買い切りライセンス](https://help.geneious.com/hc/en-us/articles/360045072611-What-is-Geneious-Prime)も公式サポートに記載されています。「すべての利用者が毎年課金される」とは言えません。同様に、ChromasProをサブスクリプション専用と説明するのも正確ではありません。

## GitHubで公開されている関連ツール

| プロジェクト | 用途と操作環境 | namiyomiとの違い |
| --- | --- | --- |
| **[sangeranalyseR](https://github.com/roblanf/sangeranalyseR)** | R/Bioconductor上でコンティグ作成を自動化し、ローカルのShiny画面で波形やトリミングを確認。FASTA・HTMLレポート出力にも対応。 | Rで処理条件を再現する用途に適しています。[公式チュートリアル](https://sangeranalyser.readthedocs.io/en/latest/)は手動の塩基編集を別ソフトで行うよう案内しています。namiyomiは、Rを起動せずに画面上で塩基を直接編集できます。無償・オープンソースですが、上流READMEはGPL-2、LICENSEファイルはMITの記載になっているため、利用するリリースのファイルを確認してください。 |
| **[Tracy](https://github.com/gear-genomics/tracy)** | ベースコール、アラインメント、アセンブリ、混合波形の分離などを扱う**コマンドライン**ツール。結果を確認する関連Webアプリも提供。 | スクリプト処理や、namiyomiの対象外の解析に向く構成です。プロジェクトを保存して塩基を編集するデスクトップGUIと同一視すべきではありません。無償・BSD-3-Clause。[公式ドキュメント](https://www.gear-genomics.com/docs/tracy/)でCLIとWebアプリを区別しています。 |

READMEやリリースの記載日だけを根拠に、他のプロジェクトを「開発停止」「使えない」とは判断していません。特定の配布物が手元のOSで動くかどうかは、別途確認が必要です。

## namiyomiを選ぶ理由と、その範囲

| 必要なこと | namiyomiで用意していること | 比較するときの注意 |
| --- | --- | --- |
| 大きなソフトの操作を覚えずに使い始める | 機能をAB1からFASTAまでの作業（自動トリミング付きの取り込み、手動・AUTOのコンティグ作成、レビューフラグ、Undo付きの編集、検索、出力）に絞り、設定項目も最小限。 | 設計方針であり、使いやすさを他製品と測定比較した結果ではありません。大きなソフトには、namiyomiが意図的に省いた解析機能があります。 |
| 継続的なライセンス料をかけずに使う | MITでアプリ全体とソースを公開。課金による有効化や期限更新は不要。 | 無償の選択肢は他にもあります。有償製品の解析範囲やサポートまで同等だという意味ではありません。 |
| 波形を見ながらコンセンサスを直す | コンセンサス、元リード、品質バー、フラグを同じ画面に表示。元リードの編集を再計算し、Undoも可能。 | ChromasProやGeneiousにも、波形とコンティグを編集する機能があります。namiyomiだけの機能とは説明しません。 |
| 命名規則が不統一でも手動で組み立てる | 任意の2本以上を選択し、両方向を調べて作成。取り込みだけでは自動グループ化しない。 | 選択したリードが同一個体・同一領域かどうかは利用者が確認します。 |
| 規則的なファイルを一括処理する | AUTOのルールは「ファイル名の最初の`_`より前」の1つだけで、設定は不要。既存コンティグで使用済みのリードは保護。 | 名前のどの部分を使うかを選びたい場合は、Sequencher、CodonCode Aligner、Geneious（いずれも有償）やRのツールに設定可能なルールがあります。 |
| 確認中の配列をその場で検索する | コンセンサス直上のFindで両鎖を検索し、オレンジ色で表示。 | 操作位置の設計上の特徴です。計算速度が他製品より速いと検証したものではありません。検索はギャップなしで、IUPACも文字どおりに照合します。 |
| Macでも同じ目的のアプリを使う | Pythonを別途準備しなくてよいApple Siliconアプリを配布。Windows版も同じ作業を対象に構成。 | Macは15以降のみで、Intel版・Linux版はありません。Windows版は試験公開で、両OSとも発行元証明書によるコード署名はありません。 |

namiyomiは、作業全体が1つの画面に収まるよう、機能を意図的に少なくしています。自分の作業に合うかどうかは、付属の合成デモプロジェクトで手早く確かめられます。「検索が速い」「アセンブリが正確」「メモリ使用量が少ない」といった主張には、同じデータ・条件による測定と再現可能な比較が必要です。ver 0.1.0では、そのようなベンチマーク結果は主張していません。
