import argparse
import time

import torch
import torch.nn.functional as F
from datasets import load_dataset
from transformers import (
    ViTForImageClassification,
    ViTImageProcessor,
)

from optimum.quanto import (
    Calibration,
    QTensor,
    freeze,
    qfloat8,
    qint4,
    qint8,
    quantize,
)


def test(model, device, test_loader):
    
    model.to(device)
    model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        start = time.time()
        for batch in test_loader:
            data, target = batch["pixel_values"], batch["labels"]
            data, target = data.to(device), target.to(device)
            output = model(data).logits
            # print("*****I am after output", output)
            if isinstance(output, QTensor):
                output = output.dequantize()
            test_loss += F.nll_loss(output, target, reduction="sum").item()  # sum up batch loss
            pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log-probability
            correct += pred.eq(target.view_as(pred)).sum().item()
        end = time.time()

    test_loss /= len(test_loader.dataset)

    print(
        "\nTest set evaluated in {:.2f} s: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n".format(
            end - start, test_loss, correct, len(test_loader.dataset), 100.0 * correct / len(test_loader.dataset)
        )
    )


def keyword_to_itype(k):
    return {"none": None, "int4": qint4, "int8": qint8, "float8": qfloat8}[k]


def main():
    parser = argparse.ArgumentParser(description="ViT PETS Example")
    parser.add_argument("--model", type=str, default="super-j/vit-base-pets")
    parser.add_argument("--device", type=str, default=None, help="The device to use for evaluation.")
    parser.add_argument("--weights", type=str, default="int8", choices=["int4", "int8", "float8"])
    parser.add_argument("--activations", type=str, default="int8", choices=["none", "int8", "float8"])
    args = parser.parse_args()

    # torch.manual_seed(args.seed)

    dataset_kwargs = {}

    if args.device is None:
        if torch.cuda.is_available():
            device = torch.device("cuda")
            cuda_kwargs = {"num_workers": 1, "pin_memory": True, "shuffle": True}
            dataset_kwargs.update(cuda_kwargs)
        elif torch.backends.mps.is_available():
            device = torch.device("cpu")
        else:
            device = torch.device("cpu")
    else:
        device = torch.device(args.device)

    # load  the processor and model
    model_name = args.model
    processor = ViTImageProcessor.from_pretrained(model_name)
    model = ViTForImageClassification.from_pretrained(model_name)

    def transform(data_batch):
        # Take a list of PIL images and turn them to pixel values
        inputs = processor(data_batch["image"], return_tensors="pt")

        # Don't forget to include the labels!
        inputs["labels"] = data_batch["label"]
        return inputs

    ds = load_dataset("rokmr/pets")
    prepared_ds = ds.with_transform(transform)
    test_loader = torch.utils.data.DataLoader(prepared_ds["test"], **dataset_kwargs)
    print("Model before quantization...")
    test(model, device, test_loader)
    weights = keyword_to_itype(args.weights)
    activations = keyword_to_itype(args.activations)
    quantize(model, weights=weights, activations=activations)
    if activations is not None:
        print("Calibrating ...")
        with Calibration():
            test(model, device, test_loader)
    print(f"Quantized model (w: {args.weights}, a: {args.activations})")
    test(model, device, test_loader)
    print("Quantized frozen model")
    freeze(model)
    test(model, device, test_loader)


if __name__ == "__main__":
    main()
