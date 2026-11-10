"""
News reports as text for the chat (and the terminal).

Every story shows its headline, publisher, date and link, how many
independent publishers reported it, official sources, corrections and
differences between accounts. Crime reports say what stage the legal
process has reached and that nothing is proven before a court decides.
Every report ends with what was searched, what wasn't, and that it isn't
every report published. Nothing is ever added that a source didn't say.
"""

from datetime import datetime

from aurora.news.article import GOOGLE_NEWS, Article, Story
from aurora.news.service import Investigation, NewsReport, compare, investigate

LEGAL_NOTES = {
    "under investigation": "Under investigation, as reported. No one has been found guilty.",
    "alleged": "Allegations, as reported. Not proven in court.",
    "arrested": "An arrest is reported. An arrest is not a finding of guilt.",
    "charged": "Charges are reported. Not proven in court.",
    "on trial": "A trial is reported. Not proven until the court decides.",
    "convicted": "A conviction is reported (an appeal may still be possible).",
    "acquitted": "An acquittal is reported: the court did not find the accused guilty.",
}


def when(article: Article) -> str:
    if article.published is None:
        return "date not given"
    return f"{article.published:%d %b %Y, %H:%M}"


def link(article: Article) -> str:
    if article.method == GOOGLE_NEWS and not article.original_url_known:
        return f"{article.url} (via Google News)"
    return article.url


def checks(story: Story) -> list[str]:
    notes = []
    independent = story.independent
    if len(independent) <= 1:
        notes.append("Only one source found so far: not independently confirmed.")
    else:
        notes.append(f"Reported by {len(independent)} independent publishers: {', '.join(independent[:5])}"
                     + (" and others." if len(independent) > 5 else "."))
    syndicated = sorted({a.agency for a in story.articles if a.agency})
    if syndicated:
        notes.append(f"Includes agency copy ({', '.join(syndicated)}), counted once.")
    if story.official:
        notes.append("Official or primary source: " + ", ".join(sorted({a.domain for a in story.official})) + ".")
    if story.legal_stage:
        notes.append(LEGAL_NOTES[story.legal_stage])
    elif story.allegation:
        notes.append("Contains claims or allegations, as reported.")
    if story.corrected:
        notes.append("A report says it was corrected or updated: check the latest version.")
    for difference in story.differences:
        notes.append(f"Accounts differ: {difference}.")
    lead = story.lead
    if lead.event_date and lead.published and lead.event_date.date() != lead.published.date():
        notes.append(f"Event date given in the report: {lead.event_date:%d %b %Y}.")
    return notes


def story_text(number: int, story: Story) -> str:
    lead = story.lead
    lines = [f"{number}. {lead.title}",
             f"   {lead.publisher} · {when(lead)}" + (f" ({lead.date_note})" if lead.date_note else "")
             + (f" · by {lead.author}" if lead.author else ""),
             f"   {link(lead)}"]
    if lead.summary:
        lines.append(f"   {lead.summary}")
    lines += [f"   - {note}" for note in checks(story)]
    return "\n".join(lines)


def coverage(report: NewsReport) -> str:
    parts = []
    searched = report.searched
    if searched:
        feeds = [r for r in searched if r.source.endswith("(feed)")]
        others = [r for r in searched if not r.source.endswith("(feed)")]
        names = [f"{r.source} ({len(r.articles)})" for r in others]
        if feeds:
            names.append(f"{len(feeds)} publisher feed{'s' if len(feeds) != 1 else ''} "
                         f"({sum(len(r.articles) for r in feeds)} matching)")
        parts.append("Searched: " + ", ".join(names) + ".")
    for result in report.problems:
        parts.append(f"Not searched: {result.note or result.source}.")
    for result in report.skipped:
        parts.append(f"Not searched: {result.note}.")
    if report.duplicates:
        parts.append(f"{report.duplicates} duplicate link{'s were' if report.duplicates != 1 else ' was'} merged.")
    if report.out_of_range:
        parts.append(f"{report.out_of_range} item{'s' if report.out_of_range != 1 else ''} outside the dates "
                     f"(or with no date) left out.")
    parts.append("These are the reports these sources returned, not every report published: paywalled, "
                 "unindexed and region-restricted sources may be missing. Links open the publishers' own pages; "
                 "summaries are the publishers' own descriptions.")
    return "Coverage: " + " ".join(parts)


def search_text(report: NewsReport, limit: int) -> str:
    query = report.query
    header = f"News: {query.describe()}."
    if query.publishers:
        header += f" Only from: {', '.join(query.publishers)}."
    if query.exclude:
        header += f" Not from: {', '.join(query.exclude)}."
    if not report.stories:
        reason = ("No source could be searched." if not report.searched
                  else "I found no reports matching that in the sources searched.")
        return f"{header}\n\n{reason} I won't fill the gap with guesses.\n\n{coverage(report)}"
    body = "\n\n".join(story_text(i, s) for i, s in enumerate(report.stories[:limit], 1))
    more = len(report.stories) - limit
    tail = f"\n\n({more} more stor{'ies' if more != 1 else 'y'} found; the News Centre shows them all.)" if more > 0 else ""
    return f"{header}\n\n{body}{tail}\n\n{coverage(report)}"


def compare_text(report: NewsReport, limit: int) -> str:
    publishers, shared, only = compare(report)
    header = f"Comparing publishers: {report.query.describe()}."
    if len(publishers) < 2:
        return (f"{header}\n\nI found fewer than two publishers' reports to compare."
                f"\n\n{coverage(report)}")
    lines = [header, f"Publishers compared: {', '.join(publishers)}.", ""]
    if shared:
        lines.append("Stories covered by more than one of them:")
        for number, story in enumerate(shared[:limit], 1):
            lines.append(f"{number}. {story.lead.title}")
            for article in story.articles:
                if article.publisher in publishers:
                    lines.append(f"   {article.publisher}: \"{article.title}\" ({when(article)}) {link(article)}")
            lines += [f"   - {note}" for note in checks(story)[1:]]
    else:
        lines.append("None of their stories overlapped in what was found.")
    lines.append("")
    for publisher in publishers:
        own = only.get(publisher) or []
        if own:
            lines.append(f"Only {publisher}: " + "; ".join(f"\"{s.lead.title}\"" for s in own))
    lines += ["", coverage(report)]
    return "\n".join(lines)


def investigation_text(report: NewsReport, limit: int = 15) -> str:
    found: Investigation = investigate(report)
    query = report.query
    publishers = {a.publisher for a in report.articles}
    header = (f"Investigation: {query.topic or query.describe()} ({query.period or query.dates()}; "
              f"{len(report.articles)} reports from {len(publishers)} publishers).")
    if not report.stories:
        return f"{header}\n\nI found no reports on this in the sources searched.\n\n{coverage(report)}"
    lines = [header, ""]
    if found.earliest:
        lines.append(f"Earliest report found: {when(found.earliest)}: \"{found.earliest.title}\" "
                     f"({found.earliest.publisher}) {link(found.earliest)}")
        lines.append("(Earliest among the sources searched; earlier reports may exist.)")
        lines.append("")
    lines.append("Timeline:")
    for day, story, item in found.timeline[:limit]:
        same_day = {a.publisher for a in story.articles if a.published and a.published.date() == day}
        others = len(same_day) - 1
        stage = f" [{item.legal_stage}, as reported]" if item.legal_stage else ""
        lines.append(f"  {day:%d %b %Y}: {item.title} ({item.publisher}"
                     + (f" and {others} other{'s' if others != 1 else ''}" if others > 0 else "") + f"){stage}")
        lines.append(f"     {link(item)}")
    if len(found.timeline) > limit:
        lines.append(f"  ... {len(found.timeline) - limit} more dates in the News Centre.")
    lines.append("")
    if found.official:
        lines.append("Official and primary sources:")
        lines += [f"  {when(a)}: {a.title} ({a.domain}) {link(a)}" for a in found.official[:5]]
    else:
        lines.append("Official and primary sources: none found among these reports.")
    if found.legal:
        stages = " -> ".join(f"{stage} ({day:%d %b})" for stage, day, _ in found.legal)
        lines.append(f"Legal process as reported (first report of each stage): {stages}.")
        final = found.legal[-1][0]
        lines.append(LEGAL_NOTES[final])
    if found.corrections:
        lines.append("Corrections and updates: " + "; ".join(
            f"\"{a.title}\" ({a.publisher}, {when(a)})" for a in found.corrections[:5]))
    if found.differences:
        lines.append("Where accounts differ: " + "; ".join(found.differences[:5]))
    lines += ["", coverage(report)]
    return "\n".join(lines)


def report_text(report: NewsReport, limit: int) -> str:
    if report.query.mode == "investigate":
        return investigation_text(report, max(limit, 15))
    if report.query.mode == "compare":
        return compare_text(report, limit)
    return search_text(report, limit)
