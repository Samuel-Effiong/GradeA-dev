# Vendored KaTeX assets

KaTeX v0.16.11 (MIT licensed, see LICENSE), vendored locally rather than
loaded from a CDN so that assignments/pdf_renderer.py can typeset LaTeX
math when generating a PDF without any outbound network dependency or
third-party availability requirement at render time.

Only what's needed for server-side rendering is included:
- katex.min.css, katex.min.js
- contrib/auto-render.min.js
- fonts/*.woff2 (woff/ttf fallback formats are intentionally omitted -
  browsers only ever fetch the first font format listed in an @font-face
  rule that they support, and every modern Chromium build supports woff2,
  so the other formats would never be requested)

To upgrade: download the katex npm package tarball for the new version
and copy the same files across, e.g.:

    curl -L -o katex.tgz https://registry.npmjs.org/katex/-/katex-<version>.tgz
    tar -xzf katex.tgz
    cp package/dist/katex.min.css assignments/vendor/katex/
    cp package/dist/katex.min.js assignments/vendor/katex/
    cp package/dist/contrib/auto-render.min.js assignments/vendor/katex/contrib/
    cp package/dist/fonts/*.woff2 assignments/vendor/katex/fonts/
    cp package/LICENSE assignments/vendor/katex/LICENSE
