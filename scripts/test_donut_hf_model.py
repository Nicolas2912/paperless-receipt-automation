#!/usr/bin/env python3
import argparse, json, re, sys
from PIL import Image
import torch
from transformers import DonutProcessor, VisionEncoderDecoderModel

def load(model_id: str):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = DonutProcessor.from_pretrained(model_id)
    model = VisionEncoderDecoderModel.from_pretrained(model_id).to(device)
    return processor, model, device

def infer(image_path: str, model_id: str, task_prompt: str | None, max_len: int | None):
    processor, model, device = load(model_id)

    # Default prompt: V2 uses <s_receipt>; V1 (or CORD) used <s_cord-v2>.
    if task_prompt is None:
        task_prompt = "<s_receipt>"

    image = Image.open(image_path).convert("RGB")
    pixel_values = processor(image, return_tensors="pt").pixel_values.to(device)

    model.eval()
    with torch.no_grad():
        dec_in = processor.tokenizer(task_prompt, add_special_tokens=False, return_tensors="pt").input_ids.to(device)
        gen = model.generate(
            pixel_values,
            decoder_input_ids=dec_in,
            max_length=max_len or model.decoder.config.max_position_embeddings,
            pad_token_id=processor.tokenizer.pad_token_id,
            eos_token_id=processor.tokenizer.eos_token_id,
            bad_words_ids=[[processor.tokenizer.unk_token_id]],
            early_stopping=True,
            return_dict_in_generate=True,
        )

    seq = processor.batch_decode(gen.sequences)[0]
    seq = seq.replace(processor.tokenizer.eos_token, "").replace(processor.tokenizer.pad_token, "")
    seq = re.sub(r"<.*?>", "", seq, count=1).strip()  # remove first task token
    parsed = processor.token2json(seq)
    print(json.dumps(parsed, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="Path to receipt image (jpg/png)")
    ap.add_argument("--model", default="to-be/donut-base-finetuned-invoices",
                    help="HF model id (e.g., AdamCodd/donut-receipts-extract)")
    ap.add_argument("--prompt", default=None,
                    help="Task prompt (default: <s_receipt>; use <s_cord-v2> for V1/CORD)")
    ap.add_argument("--max-length", type=int, default=None)
    args = ap.parse_args()

    try:
        infer(args.image, args.model, args.prompt, args.max_length)
    except Exception as e:
        sys.stderr.write(f"[error] {e}\n"
                         "Tip: If access is denied, accept the model terms on HF or try "
                         "--model naver-clova-ix/donut-base-finetuned-cord-v2 --prompt \"<s_cord-v2>\".\n")
        sys.exit(1)
