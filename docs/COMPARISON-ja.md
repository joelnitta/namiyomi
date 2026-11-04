# サンガー配列ソフトの使い分け

[English](COMPARISON.md) · [namiyomiの説明へ](../README-ja.md)

namiyomiは、AB1の波形を確認し、同一個体・同一領域のリードからコンティグを作り、編集した配列をFASTAで取り出す用途に絞っています。強みは、作業に必要な画面をまとめた構成、元リードとコンセンサスを同時に編集・確認できること、アプリの利用にライセンス料や更新料がかからないことです。

以下は、**2026年11月4日**に開発元の公式説明とリポジトリを確認してまとめた比較です。速度、精度、メモリ使用量、使いやすさを測定したランキングではありません。料金体系と対応OSは変更される場合があります。

## デスクトップアプリ

| ソフト | 波形確認・コンティグ作成 | 自動処理・検索 | 配布・料金体系 |
| --- | --- | --- | --- |
| **namiyomi 0.0.1** | 2本以上を選択して作成。コンセンサスの下に元リードの波形を表示し、元リードの編集からコンセンサスを再計算。AB1へ戻して作り直す操作も用意。 | 未使用リードを、元のファイル名の最初の`_`より前でまとめるAUTO。検索は両鎖、80～100%、ギャップなしの文字一致。 | MITで無償。Apple Silicon/macOS 15以降、Windows 10/11 x64試験版。Windows実機での一連の操作確認は未完了。Macは未公証。 |
| **[ChromasPro](https://technelysium.com.au/wp/chromaspro/)** | グラフィカルな重複アセンブリ、コンセンサス、曖昧な位置の表示、品質トリミング、整列した波形の編集。[更新履歴](https://technelysium.com.au/wp/chromaspro-version-history/)には、選択配列だけのアセンブリも記載。 | **検索機能は存在します。** 完全一致・最適アラインメント検索と、コンセンサス検索に対応。namiyomiと同じ接頭辞ルールのAUTOは、参照した公開説明では確認できませんでした。「機能がない」という意味ではありません。 | 公式案内はWindows。有償ですが、[月額と買い切りの両方](https://technelysium.com.au/wp/purchase/)があり、試用版も提供。 |
| **[Geneious Prime](https://www.geneious.com/tutorials/assembling-chromatograms)** | Sangerのトリミング、de novo・参照配列へのアセンブリ、波形・コンセンサス編集を含む統合環境。 | [Assemble by Name](https://manual.geneious.com/en/latest/AssemblyMapping.html)は、区切り文字と名前の何番目を使うかを指定してグループ化。[ワークフロー](https://www.geneious.com/tutorials/automated-workflows)で、トリミングから下流解析まで連結可能。 | Windows/macOS/Linux。[現行の公開プラン](https://www.geneious.com/pricing)は年額制。試用版あり。旧バージョンには買い切りライセンスも存在。 |
| **[SeqTrace](https://github.com/stuckyb/seqtrace)** | F/Rの波形を確認・編集し、ペアのコンセンサスを作る用途に絞ったGUI。トリミングとFASTA出力にも対応。 | READMEはF/Rの自動識別、アラインメント、プロジェクト全体のコンセンサス処理を案内。 | 無償、ソースの表記は[GPL-3.0以降](https://github.com/stuckyb/seqtrace/blob/master/src/seqtrace/__init__.py)。Linux/Windows/macOSを案内していますが、[導入説明](https://github.com/stuckyb/seqtrace/wiki/Installation)はPython 2/PyGTK時代の内容を含みます。今回、現行OSでの互換性は検証していません。 |
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
| 継続的なライセンス料をかけずに使う | MITでアプリ全体とソースを公開。課金による有効化や期限更新は不要。 | 無償の選択肢は他にもあります。有償製品の解析範囲やサポートまで同等だという意味ではありません。 |
| 波形を見ながらコンセンサスを直す | コンセンサス、元リード、品質バー、フラグを同じ画面に表示。元リードの編集を再計算し、Undoも可能。 | ChromasProやGeneiousにも、波形とコンティグを編集する機能があります。namiyomiだけの機能とは説明しません。 |
| 命名規則が不統一でも手動で組み立てる | 任意の2本以上を選択し、両方向を調べて作成。取り込みだけでは自動グループ化しない。 | 選択したリードが同一個体・同一領域かどうかは利用者が確認します。 |
| 規則的なファイルを一括処理する | 接頭辞によるAUTOで未使用リードを処理し、既存コンティグを保護。 | ルールは意図的に単純です。GeneiousやRのツールには、より柔軟なグループ化・処理条件があります。 |
| 確認中の配列をその場で検索する | コンセンサス直上のFindで両鎖を検索し、オレンジ色で表示。 | 操作位置の設計上の特徴です。計算速度が他製品より速いと検証したものではありません。検索はギャップなしで、IUPACも文字どおりに照合します。 |
| Macでも同じ目的のアプリを使う | Pythonを別途準備しなくてよいApple Siliconアプリを配布。Windows版も同じ作業を対象に構成。 | Macは15以降のみで、Intel版・Linux版はありません。Windows版は試験公開で、両OSとも発行元証明書によるコード署名はありません。 |

作業を絞った画面を使いやすいと感じるかどうかは、利用者が判断することです。「検索が速い」「アセンブリが正確」「圧倒的に使いやすい」といった主張には、同じデータ・条件による測定と再現可能な比較が必要です。ver 0.0.1では、そのようなベンチマーク結果は主張していません。
