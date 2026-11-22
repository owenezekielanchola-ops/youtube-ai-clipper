export interface CampaignRule {
  title: string;
  description: string;
}

export interface CampaignConfig {
  id: string;
  label: string;
  overlayText: string;
  overlayPosition: 'bottom_left';
  editingRules: string[];
  requiredRules: CampaignRule[];
  prohibitedRules: CampaignRule[];
  defaultHashtags: string[];
}

export const LACY_CAMPAIGN: CampaignConfig = {
  id: 'lacy_campaign',
  label: 'Lacy Campaign',
  overlayText: 'LACY',
  overlayPosition: 'bottom_left',
  editingRules: [
    'Make the video captions engaging subtitles of what Lacy is saying and doing.',
    'Include Lacy’s name ("LACY") as an on-screen text overlay.',
    'Lacy must actually be in the edit itself—do not make Lacy a "cameo-only" intro.',
    'No "hate farming"—do not push fake or negative narratives just to stir up drama or hate.',
  ],
  requiredRules: [
    {
      title: 'Match Your Account’s Niche',
      description:
        'Your account’s normal audience shouldn’t clash completely with gaming/creator content (for example, posting Lacy clips on a bird-watching page risks getting rejected).',
    },
    {
      title: 'Mention "LACY"',
      description: 'Include Lacy’s name in your caption or on-screen text overlay.',
    },
    {
      title: 'Use the Hashtag',
      description: 'Put #LACY in your caption.',
    },
    {
      title: 'Qualify for Revenue Share',
      description:
        'Only approved videos count, and you must hit the total view requirement to get a cut of the revenue share.',
    },
    {
      title: 'Read the Pool Rules',
      description:
        "Check the campaign's pool rules to understand how pool campaigns and payouts work.",
    },
  ],
  prohibitedRules: [
    {
      title: 'Don’t Make Lacy a "Cameo-Only" Intro',
      description:
        'If you make an edit, Lacy must actually be in the edit itself—videos that only show him in the intro will be rejected.',
    },
    {
      title: 'No "Hate Farming"',
      description:
        'Don’t push fake or negative narratives just to stir up drama/hate. That can get your video rejected—or even get you banned.',
    },
    {
      title: 'No Fake Views or Engagement',
      description:
        'Don’t use bots, reposts, collab posts, or any other tricks to artificially boost views or engagement.',
    },
  ],
  defaultHashtags: ['#LACY', '#Lacy', '#clips'],
};

export const CAMPAIGNS: CampaignConfig[] = [LACY_CAMPAIGN];

export function getCampaignById(id?: string | null): CampaignConfig | null {
  if (!id) return null;
  return CAMPAIGNS.find((c) => c.id === id) ?? null;
}

export function buildDefaultPostCaption(
  campaign: CampaignConfig,
  clipTitle?: string | null,
): string {
  const cleanTitle = (clipTitle || '').trim();
  const lead = cleanTitle
    ? `${cleanTitle} — featuring ${campaign.overlayText}`
    : `Best moments featuring ${campaign.overlayText}`;
  return `${lead}\n\n${campaign.defaultHashtags.join(' ')}`;
}

export function buildCampaignAssistantNotice(
  campaign: CampaignConfig,
  clipTitle?: string | null,
): { content: string; copyableCaption: string } {
  const copyableCaption = buildDefaultPostCaption(campaign, clipTitle);
  const reqLines = campaign.requiredRules
    .map((r) => `• ${r.title}: ${r.description}`)
    .join('\n');
  const banLines = campaign.prohibitedRules
    .map((r) => `❌ ${r.title}: ${r.description}`)
    .join('\n');

  const content =
    `[${campaign.label}] Upload & Posting Requirements Ready:\n\n` +
    `Ready-to-Copy Caption (must include "LACY" & #LACY):\n"${copyableCaption.replace(/\n+/g, ' ')}"\n\n` +
    `Required Rules:\n${reqLines}\n\n` +
    `What You MUST NOT Do:\n${banLines}\n\n` +
    `Tip: You can customize "${campaign.overlayText}" location, colors & size anytime in the "Styles & Tools" tab above.`;

  return { content, copyableCaption };
}
