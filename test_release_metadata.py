"""Human-readable and machine-readable citations describe the same release."""
from pathlib import Path
import unittest
from app_info import APP_NAME,APP_VERSION


class CitationTests(unittest.TestCase):
    def test_citation_title_author_version_and_repository_are_consistent(self):
        root=Path(__file__).resolve().parent
        title='Namiyomi: Sanger chromatogram review and contig assembly'
        url='https://github.com/yoneoka-katsuhiro/namiyomi'
        cff=(root/'CITATION.cff').read_text(encoding='utf-8')
        self.assertIn('title: "'+title+'"',cff);self.assertIn('version: '+APP_VERSION,cff)
        self.assertIn('family-names: Yoneoka',cff);self.assertIn('given-names: Katsuhiro',cff)
        self.assertIn('repository-code: "'+url+'"',cff)
        for name in ('CITATION.txt','README.md','README-ja.md'):
            text=(root/name).read_text(encoding='utf-8')
            self.assertIn('Yoneoka, K. (2026).',text);self.assertIn(title,text)
            self.assertIn('(Version '+APP_VERSION+') [Computer software]. GitHub.',text)
            self.assertIn(url,text);self.assertIn('Yoneoka (2026)',text)
        self.assertEqual(APP_NAME,'namiyomi')


if __name__=='__main__':unittest.main()
