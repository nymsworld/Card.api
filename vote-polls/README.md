# Votes on kantomisplay.cc/vote

Each vote is one file here, `<id>.json` (id: lowercase letters, digits and dashes). `current.json` names the vote
that kantomisplay.cc/vote shows; any vote opens at `kantomisplay.cc/vote?poll=<id>`. The staff site
(kms-app `functions/api/site`, route `vote/<id>`) reads the same file to check votes, so a new vote needs no code change.

```json
{
  "id": "my-vote",
  "open": true,                       // false = closed: no new votes
  "title": "Vote on our <em>new look</em>",   // <em> = the glowing red words
  "intro": "What this vote is for (simple HTML: <strong>, <b>, <em>, <a>).",
  "watchTitle": "Look at the options",        // optional
  "watchHint": "Tap View to see one big.",     // optional
  "gridNote": "A short note under the options", // optional
  "frame": "phone",                   // optional: "phone" (tall phone screen), else each option's own shape
  "aspect": "1 / 1",                  // optional shape of the option tiles when frame isn't "phone"
  "options": [                        // 2 to 12, every one gets ranked
    { "id": "A", "name": "Short name", "note": "One more detail",
      "image": "/vote-img/my-vote/a.png", "bg": "#ffffff" },       // a picture (bg behind it, optional)
    { "id": "B", "name": "…", "video": "/vote-img/my-vote/b.mp4", "poster": "/vote-img/my-vote/b.jpg" },
    { "id": "C", "name": "…", "splash": { "bg": "dark", "letters": "white", "arch": false } }  // the KMS splash
  ]
}
```

Put pictures and videos in `vote-img/<id>/`. Option ids must not change once votes come in. Results show only
to someone who has voted; the full count (names and comments too) is read from D1 `site_votes`.
