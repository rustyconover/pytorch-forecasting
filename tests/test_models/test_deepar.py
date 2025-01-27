import pickle
import shutil

import pytest
import pytorch_lightning as pl
from pytorch_lightning.callbacks import EarlyStopping, ModelCheckpoint
from pytorch_lightning.loggers import TensorBoardLogger
from test_models.conftest import make_dataloaders
from torch import nn

from pytorch_forecasting.data.encoders import GroupNormalizer
from pytorch_forecasting.metrics import (
    BetaDistributionLoss,
    LogNormalDistributionLoss,
    NegativeBinomialDistributionLoss,
)
from pytorch_forecasting.models import DeepAR
from pytorch_forecasting.models.deepar.sub_modules import TimeSeriesGRU, TimeSeriesLSTM, get_cell


def _integration(
    data_with_covariates, tmp_path, gpus, cell_type="LSTM", data_loader_kwargs={}, clip_target: bool = False, **kwargs
):
    if clip_target:
        data_with_covariates["target"] = data_with_covariates["volume"].clip(1e-3, 1.0)
    else:
        data_with_covariates["target"] = data_with_covariates["volume"]
    data_loader_default_kwargs = dict(
        target="target",
        time_varying_known_reals=["price_actual"],
        time_varying_unknown_reals=["target"],
        static_categoricals=["agency"],
        add_relative_time_idx=True,
    )
    data_loader_default_kwargs.update(data_loader_kwargs)
    dataloaders_with_covariates = make_dataloaders(data_with_covariates, **data_loader_default_kwargs)
    train_dataloader = dataloaders_with_covariates["train"]
    val_dataloader = dataloaders_with_covariates["val"]
    early_stop_callback = EarlyStopping(monitor="val_loss", min_delta=1e-4, patience=1, verbose=False, mode="min")

    logger = TensorBoardLogger(tmp_path)
    checkpoint = ModelCheckpoint(filepath=tmp_path)
    trainer = pl.Trainer(
        checkpoint_callback=checkpoint,
        max_epochs=3,
        gpus=gpus,
        weights_summary="top",
        gradient_clip_val=0.1,
        callbacks=[early_stop_callback],
        fast_dev_run=True,
        logger=logger,
    )

    net = DeepAR.from_dataset(
        train_dataloader.dataset,
        cell_type=cell_type,
        learning_rate=0.15,
        log_gradient_flow=True,
        log_interval=1000,
        n_plotting_samples=100,
        **kwargs
    )
    net.size()
    try:
        trainer.fit(
            net,
            train_dataloader=train_dataloader,
            val_dataloaders=val_dataloader,
        )
        # check loading
        net = DeepAR.load_from_checkpoint(checkpoint.best_model_path)

        # check prediction
        net.predict(val_dataloader, fast_dev_run=True, return_index=True, return_decoder_lengths=True)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

    net.predict(val_dataloader, fast_dev_run=True, return_index=True, return_decoder_lengths=True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"cell_type": "GRU"},
        dict(
            loss=LogNormalDistributionLoss(),
            clip_target=True,
            data_loader_kwargs=dict(target_normalizer=GroupNormalizer(groups=["agency", "sku"], transformation="log")),
        ),
        dict(
            loss=NegativeBinomialDistributionLoss(),
            clip_target=True,
            data_loader_kwargs=dict(target_normalizer=GroupNormalizer(groups=["agency", "sku"], center=False)),
        ),
        dict(
            loss=BetaDistributionLoss(),
            clip_target=True,
            data_loader_kwargs=dict(
                target_normalizer=GroupNormalizer(groups=["agency", "sku"], transformation="logit")
            ),
        ),
    ],
)
def test_integration(data_with_covariates, tmp_path, gpus, kwargs):
    _integration(data_with_covariates, tmp_path, gpus, **kwargs)


def test_integration_for_multiple_targets(data_with_covariates, tmp_path, gpus):
    _integration(
        data_with_covariates,
        tmp_path,
        gpus,
        data_loader_kwargs=dict(time_varying_unknown_reals=["volume", "discount"], target=["volume", "discount"]),
    )


def test_get_lstm_cell():
    cell = get_cell("LSTM")(10, 10)
    assert isinstance(cell, TimeSeriesLSTM)
    assert isinstance(cell, nn.LSTM)


def test_get_gru_cell():
    cell = get_cell("GRU")(10, 10)
    assert isinstance(cell, TimeSeriesGRU)
    assert isinstance(cell, nn.GRU)


def test_get_cell_raises_value_error():
    pytest.raises(ValueError, lambda: get_cell("ABCDEF"))


@pytest.fixture
def model(dataloaders_with_covariates):
    dataset = dataloaders_with_covariates["train"].dataset
    net = DeepAR.from_dataset(
        dataset,
        learning_rate=0.15,
        log_gradient_flow=True,
        log_interval=1000,
    )
    return net


def test_pickle(model):
    pkl = pickle.dumps(model)
    pickle.loads(pkl)
