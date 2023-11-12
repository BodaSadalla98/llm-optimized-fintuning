import pathlib
from datasets import load_dataset
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, AutoTokenizer
from transformers import TrainingArguments
from trl import SFTTrainer
from evaluate import load
from peft import LoraConfig, prepare_model_for_kbit_training
from args_parser import get_args
import re
from pathlib import Path
from utils import *

import numpy as np
import evaluate

if __name__ == "__main__":
    args = get_args()

    for arg in vars(args):
        print(arg, getattr(args, arg))



    # Get the datasets
    train_dataset, val_dataset = get_datasets(train_dataset_source_path=args.train_dataset_source_path,
                                                     train_dataset_target_path=args.train_dataset_target_path,
                                                     val_dataset_source_path=args.val_dataset_source_path,
                                                     val_dataset_target_path=args.val_dataset_target_path,
                                                     field = args.field)

    model_name = args.model_name

    ## Bits and Bytes config
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        # bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True
    )

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=bnb_config,
        trust_remote_code=True
    )


    ## Enable gradient checkpointing
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()

    ## Prepare model for k-bit training
    # model = prepare_model_for_kbit_training(model)


    ## Print the number of trainable parameters
    print_trainable_parameters(model)

    ## Silence the warnings
    model.config.use_cache = False

    ## Load the tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = 'right'


    output_dir = args.output_dir

    per_device_train_batch_size =  args.per_device_train_batch_size
    per_device_val_batch_size = args.per_device_val_batch_size
    gradient_accumulation_steps = args.gradient_accumulation_steps


    epoch_steps = len(train_dataset) // (per_device_train_batch_size * gradient_accumulation_steps)


    optim = args.optim

    save_steps = args.save_steps
    logging_steps = args.logging_steps
    learning_rate = args.learning_rate
    max_grad_norm = args.max_grad_norm


    max_steps = epoch_steps * 10

    warmup_ratio = args.warmup_ratio
    lr_scheduler_type = args.lr_scheduler_type


    output_dir = args.output_dir + f"/{model_name.split('/')[-1]}"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    print(f"Saving the model to {output_dir}")


    training_arguments = TrainingArguments(
        output_dir=output_dir,
        do_eval=True,
        evaluation_strategy="steps",
        eval_steps=10,
        per_device_eval_batch_size=2,
        per_device_train_batch_size=per_device_train_batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        optim=optim,
        save_steps=save_steps,
        logging_steps=logging_steps,
        learning_rate=learning_rate,
        fp16=True,
        max_grad_norm=max_grad_norm,
        max_steps=max_steps,
        warmup_ratio=warmup_ratio,
        group_by_length=True,
        lr_scheduler_type=lr_scheduler_type,
        gradient_checkpointing=args.gradient_checkpointing,
    )


    lora_alpha = args.lora_alpha
    lora_dropout = args.lora_dropout
    lora_r = args.lora_r


    lora_target_modules = [
        "q_proj",
        "up_proj",
        "o_proj",
        "k_proj",
        "down_proj",
        "gate_proj",
        "v_proj",
    ]

    peft_config = LoraConfig(
        lora_alpha=lora_alpha,
        lora_dropout=lora_dropout,
        r=lora_r,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules = lora_target_modules
    )


    max_seq_length = args.max_seq_length

    def simple_accuracy(preds, labels):
        return (preds == labels).mean().item()

    def compute_metrics(eval_preds):
        return {"loss": 0}
        # bertscore = evaluate.load("bertscore")
        # logits, labels = eval_preds
        # predictions = np.argmax(logits, axis=-1)

        # loss = torch.nn.functional.softmax(logits)
        # return {"accuracy": simple_accuracy(predictions, labels)}
        # return {"bertscore": bertscore.compute(predictions=predictions, references=labels)}#, "perplextiy": loss}

    trainer = SFTTrainer(
        model=model,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        peft_config=peft_config,
        dataset_text_field=args.field,
        max_seq_length=max_seq_length,
        tokenizer=tokenizer,
        args=training_arguments,
        compute_metrics=compute_metrics,
        
    )


    for name, module in trainer.model.named_modules():
        if "norm" in name:
            module = module.to(torch.float32)


    if args.checkpoint_path:
        trainer.train(args.checkpoint_path)
    else:
        trainer.train()


    trainer.save_model()


    print("Done training")
    print(trainer.model)
