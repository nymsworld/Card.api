name: Update Pokemon Card Prices Daily

on:
  schedule:
    # The update is split into 4 parts, 30 minutes apart, so no single run sends
    # enough requests to the card APIs to get rate limited.
    # GitHub schedules in UTC. 06:05-07:35 UTC stays inside 1-4 AM Eastern all year:
    #   winter (EST): 1:05, 1:35, 2:05, 2:35 AM
    #   summer (EDT): 2:05, 2:35, 3:05, 3:35 AM
    # GitHub can start scheduled runs a few minutes late; the gaps leave room for that.
    - cron: '5 6 * * *'    # part 1: Double Rares, ACE SPECs, singles, Classic Collection, promos
    - cron: '35 6 * * *'   # part 2: 151 holos + first half of SV overnumbered
    - cron: '5 7 * * *'    # part 3: second half of SV overnumbered
    - cron: '35 7 * * *'   # part 4: Mega Evolution overnumbered
  # Allows you to click a button and run it manually whenever you want
  workflow_dispatch:
    inputs:
      part:
        description: 'Which part to update (all = everything in one run)'
        type: choice
        options: ['all', '1', '2', '3', '4']
        default: 'all'

permissions:
  contents: write

# Never let two price updates run at the same time
concurrency:
  group: price-update
  cancel-in-progress: false

jobs:
  sync-prices:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Dependencies
        run: |
          pip install requests

      - name: Pick Which Part to Run
        id: part
        run: |
          case "${{ github.event.schedule }}" in
            "5 6 * * *")  PART=1 ;;
            "35 6 * * *") PART=2 ;;
            "5 7 * * *")  PART=3 ;;
            "35 7 * * *") PART=4 ;;
            *)            PART="${{ github.event.inputs.part || 'all' }}" ;;
          esac
          echo "part=$PART" >> "$GITHUB_OUTPUT"

      - name: Run Price Updater
        run: |
          python update_prices.py ${{ steps.part.outputs.part }}

      - name: Commit and Push Updated prices.json
        run: |
          git config --global user.name "github-actions[bot]"
          git config --global user.email "github-actions[bot]@users.noreply.github.com"
          git add prices.json
          git commit -m "Auto-update prices.json (part ${{ steps.part.outputs.part }}) [skip ci]" || exit 0
          git pull --rebase
          git push
