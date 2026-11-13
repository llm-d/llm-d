# Documentation translations

Translated pages live at `translations/<locale>/docs/<path>` and use the same
relative path and Markdown extension as their English source in `docs/`.
For example, `translations/zh-Hans/docs/getting-started/README.mdx` follows
`docs/getting-started/README.mdx`. Keeping translations outside `docs/` prevents
them from appearing as English pages in the documentation sidebar.

Every translated Markdown page must include these front matter fields:

```yaml
translation_source: docs/getting-started/README.mdx
translation_source_blob: <Git blob ID of the English source>
```

After updating a translation, run `git hash-object docs/getting-started/README.mdx`
and record the output in `translation_source_blob`. CI runs
`python3 scripts/check_translations.py` on every pull request. If the English
source changes before its translation can be updated, set
`translation_status: stale` in the translated page. CI will report it, and the
website sync will omit that page so readers see the English source. Remove the
stale marker and update the blob ID when the translation is refreshed.

The website sync publishes available current translations at `/zh-Hans/docs/dev`.
Released docs are frozen in the website repository, so later changes to a
current translation do not alter an already published release.
