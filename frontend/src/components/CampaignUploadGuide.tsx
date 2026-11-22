import React, { useEffect, useState } from 'react';
import { AlertTriangle, Check, CheckCircle2, Copy, ShieldCheck, XCircle } from 'lucide-react';
import { buildDefaultPostCaption, type CampaignConfig } from '../config/campaigns';

interface ExportedFileMeta {
  filename: string;
  title: string;
  url: string;
}

interface CampaignUploadGuideProps {
  campaign: CampaignConfig;
  projectName: string;
  exportedFiles: ExportedFileMeta[];
}

export const CampaignUploadGuide: React.FC<CampaignUploadGuideProps> = ({
  campaign,
  projectName,
  exportedFiles,
}) => {
  const defaultTitle = exportedFiles[0]?.title || projectName;
  const [captionDraft, setCaptionDraft] = useState(() =>
    buildDefaultPostCaption(campaign, defaultTitle),
  );
  const [copied, setCopied] = useState(false);
  const [checkedItems, setCheckedItems] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setCaptionDraft(buildDefaultPostCaption(campaign, defaultTitle));
  }, [campaign, defaultTitle]);

  const handleCopyCaption = async () => {
    try {
      await navigator.clipboard.writeText(captionDraft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard fallback ignored if permission denied
    }
  };

  const toggleCheck = (key: string) => {
    setCheckedItems((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="space-y-3 pt-3 border-t border-[#272f40]">
      {/* Banner Header */}
      <div className="flex items-center justify-between gap-2 bg-violet-500/10 border border-violet-400/25 rounded-xl px-3.5 py-2.5">
        <div className="flex items-center gap-2.5">
          <ShieldCheck className="w-4 h-4 text-violet-300 shrink-0" />
          <div>
            <div className="text-xs font-semibold text-violet-200">
              {campaign.label} — Manual Upload Instructions
            </div>
            <div className="text-xs text-violet-300/80 mt-0.5 leading-relaxed">
              On-screen &ldquo;{campaign.overlayText}&rdquo; overlay burned into bottom-left. Follow these rules when posting:
            </div>
          </div>
        </div>
      </div>

      {/* Ready-to-Copy Post Caption */}
      <div className="bg-[#12161f] border border-[#272f40] rounded-xl p-3 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold text-slate-200">
            Ready-to-Copy Post Caption (includes &ldquo;LACY&rdquo; &amp; &ldquo;#LACY&rdquo;)
          </span>
          <button
            type="button"
            onClick={handleCopyCaption}
            className={`px-2.5 py-1 rounded-lg text-xs font-semibold flex items-center gap-1 transition ${
              copied
                ? 'bg-emerald-500 text-slate-950'
                : 'bg-amber-400 hover:bg-amber-300 text-slate-950'
            }`}
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                Copy Caption
              </>
            )}
          </button>
        </div>
        <textarea
          rows={3}
          value={captionDraft}
          onChange={(e) => setCaptionDraft(e.target.value)}
          className="w-full bg-[#1c212d] border border-[#272f40] focus:border-amber-400/70 rounded-lg p-2.5 text-xs text-slate-100 leading-relaxed outline-none resize-y"
        />
      </div>

      {/* Required Rules with Optional Self-Check */}
      <div className="bg-emerald-500/5 border border-emerald-500/25 rounded-xl p-3 space-y-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300">
          <CheckCircle2 className="w-4 h-4" />
          Required Rules
        </div>
        <ul className="space-y-2">
          {campaign.requiredRules.map((rule, idx) => {
            const key = `req_${idx}`;
            const checked = Boolean(checkedItems[key]);
            return (
              <li key={rule.title} className="text-xs text-slate-300 leading-relaxed">
                <label className="flex items-start gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleCheck(key)}
                    className="mt-1 rounded border-[#272f40] bg-[#1c212d] accent-emerald-400 shrink-0"
                  />
                  <span className={checked ? 'line-through text-slate-500' : ''}>
                    <strong className="text-slate-100">{rule.title}:</strong> {rule.description}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </div>

      {/* What You MUST NOT Do (Red X's) */}
      <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-3 space-y-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-red-300">
          <AlertTriangle className="w-4 h-4" />
          What You MUST NOT Do (Red X&apos;s)
        </div>
        <ul className="space-y-2">
          {campaign.prohibitedRules.map((rule) => (
            <li
              key={rule.title}
              className="flex items-start gap-2 text-xs text-red-100/90 leading-relaxed"
            >
              <XCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
              <span>
                <strong className="text-red-200">{rule.title}:</strong> {rule.description}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};
